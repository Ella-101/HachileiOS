#!/usr/bin/env python3

#
# python script that tests xv6 without having to boot it and type to its shell
#
# ./test-xv6.py usertests  (runs usertests)
# ./test-xv6.py -q usertests (runs the quick tests of usertests)
# ./test-xv6.py crash  (runs the crash tests)
# ./test-xv6.py log (runs the log crash test)

import argparse, os, inspect, re, signal, subprocess, sys, time
from subprocess import run

sys.stdout.reconfigure(line_buffering=True)

parser = argparse.ArgumentParser()
parser.add_argument('testrex', help="test name or regular expression")
parser.add_argument("-q", action='store_true', help="usertests quick")
args = parser.parse_args()

class QEMU(object):

    def __init__(self, reset=False):
        if reset:
            self.build_xv6()
            self.reset_fs()
        q = ["make", "qemu"]
        self.proc = subprocess.Popen(q, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT)
        os.set_blocking(self.proc.stdout.fileno(), False)
        self.output = ""
        self.outbytes = bytearray()
        self.reported = 0
        time.sleep(1)

    def reset_fs(self):
        try:
            run(["rm", "-f", "fs.img"], check=True)
            run(["make", "fs.img"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Command failed with exit code {e.returncode}")

    def build_xv6(self):
        try:
            run(["make", "kernel/kernel"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Command failed with exit code {e.returncode}")

    def save_output(self):
      try:
        with open("test-xv6.out", "w") as f:
            f.write(self.output)
            f.close()
      except OSError as e:
        print("Provided a bad results path. Error:", e)     
        
    def cmd(self, c):
        if isinstance(c, str):
            c = c.encode('utf-8')
        self.proc.stdin.write(c)
        self.proc.stdin.flush()
        
    def crash(self):
        ps = run(['ps', '-opid', '--no-headers', '--ppid', str(self.proc.pid)], stdout=subprocess.PIPE, encoding='utf8')
        kids = [int(line) for line in ps.stdout.splitlines()]
        if len(kids) == 0:
            print("no qemu")
            sys.exit(1)
        print("kill", kids[0])
        os.kill(kids[0], signal.SIGKILL)

    def stop(self):
        self.proc.terminate()

    def read(self):
        while True:
            try:
                buf = os.read(self.proc.stdout.fileno(), 4096)
            except BlockingIOError:
                break
            if len(buf) == 0:  # qemu exited
                break
            self.outbytes.extend(buf)
        self.output = self.outbytes.decode("utf-8", "replace")

    def lines(self):
        return self.output.splitlines()

    # Wait until the guest has reached a shell prompt, so that a command
    # written to the console is actually read by sh.  Booting under
    # emulation can take several seconds on a loaded machine, and the old
    # fixed one-second sleep was not always enough: the logstress command
    # could still be sitting in the UART when the harness killed qemu,
    # leaving no pending log and no files to recover.
    def wait_shell(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
            self.read()
            for line in self.lines():
                if re.match(r'\$ *$', line):
                    return
        return

    def error(self, *regexps):
        print("FAIL: match failed", regexps)
        self.save_output()
        self.stop()
        sys.exit(1)

    def match(self, *regexps, exit=True):
        found = False
        for line in self.lines():
            if any(re.match(r, line) for r in regexps):
                print(line)
                found = True
        if not found and exit:
            self.error(*regexps)
        return found

    # Print the lines matching regexp that have arrived since the last
    # call.  A trailing partial line is left for the next call, so that
    # each line is printed once, after all of it has been read.
    def progress(self, regexp):
        end = self.output.rfind("\n") + 1
        if end <= self.reported:
            return
        for line in self.output[self.reported:end].splitlines():
            if re.match(regexp, line):
                print(line)
        self.reported = end

    def monitor(self, *regexps, progress="", timeout, fail=True):
        deadline = time.time() + timeout
        while True:
            time.sleep(1)
            timeleft = deadline - time.time()
            if timeleft < 0:
                if fail:
                    self.error(*regexps)
                return False
            self.read()
            if progress:
                self.progress(progress)
            if self.match(*regexps, exit=False):
                return True

def crash_log():
    q = QEMU(True)
    q.wait_shell()
    q.cmd("logstress f0 f1 f2 f3 f4 f5\n")
    # logstress forks six children that each create one file; give them
    # long enough to all get going before pulling the plug.  If the crash
    # lands too early the recovered image may be missing f5, which the
    # caller now treats as a retryable failure rather than a hard error.
    time.sleep(5)
    q.crash()
    q.stop()

def recover_log():
    q = QEMU()
    q.wait_shell()
    q.read()
    ok = q.match('^recovering', exit=False)
    if ok:
        q.cmd("ls\n")
        # A missing f5 means this attempt crashed before logstress created
        # all its files; report it so test_log can retry instead of
        # aborting the whole run.
        ok = q.monitor('f5', timeout=10, fail=False)
    q.stop()
    return ok

def forphan():
    q = QEMU(True)
    q.wait_shell()
    q.cmd("forphan\n")
    q.monitor('wait', timeout=30)
    q.crash()
    q.stop()

def dorphan():
    q = QEMU(True)
    q.wait_shell()
    q.cmd("dorphan\n")
    q.monitor('wait', timeout=30)
    q.crash()
    q.stop()

def recover_orphan():
    q = QEMU()
    q.monitor('^ireclaim', timeout=30)
    q.stop()

def test_log():
    print("Test recovery of log")
    for i in range(20):
        crash_log()
        ok = recover_log()
        if ok:
            print("OK")
            return
        print("log attempt ", i+1)
    print("FAIL")
    sys.exit(1)
    
def test_forphan():
    print("Test recovery of an orphaned file")
    forphan()
    recover_orphan()
    print("OK")

def test_dorphan():
    print("Test recovery of an orphaned file")
    dorphan()
    recover_orphan()
    print("OK")

def test_crash():
    test_log()
    test_forphan()
    test_dorphan()

def test_usertests(test=""):
    timeout = 600
    opt = ""
    if args.q:
        opt = " -q"
        timeout = 300
    elif test != "":
        opt += " " + test
    q = QEMU(True)
    q.wait_shell()
    q.cmd("usertests" + opt + "\n")
    q.monitor('^ALL TESTS PASSED', progress='test', timeout=timeout)
    q.stop()

def main():
    print(args)
    rex = r'%s' % args.testrex
    funcs = [(obj,name) for name,obj in inspect.getmembers(sys.modules[__name__]) 
                     if (inspect.isfunction(obj) and 
                         name.startswith('test'))]
    none = True
    for (f,n) in funcs:
        if re.search(rex, n):
            none = False
            f()
    if none:
        test_usertests(test=args.testrex)

main()
