#!/usr/bin/env python3

#
# python script that tests xv6 without having to boot it and type to its shell
#
# ./test-xv6.py usertests  (runs usertests)
# ./test-xv6.py -q usertests (runs the quick tests of usertests)
# ./test-xv6.py crash  (runs the crash tests)
# ./test-xv6.py log (runs the log crash test)

import argparse, os, json, re, signal, socket, struct, subprocess, sys, tempfile, time
from subprocess import run
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

parser = argparse.ArgumentParser()
parser.add_argument('testrex', help="test name or regular expression")
parser.add_argument("-q", action='store_true', help="usertests quick")

parser.add_argument("--cpus", type=int, choices=(1, 3), default=int(os.environ.get("CPUS", "3")))
parser.add_argument("--repeat", type=int, default=1)
parser.add_argument("--timeout", type=float, default=120, help="dedicated test timeout in seconds")
parser.add_argument("--artifacts", default="test-results")
config = argparse.Namespace(cpus=int(os.environ.get("CPUS", "3")), artifacts="test-results")
sessions = []

class QEMU(object):

    def __init__(self, reset=False, control=False):
        self.commands = []
        self.logbase = Path(config.artifacts) / (str(time.time_ns()) + "-cpu" + str(config.cpus))
        self.logbase.parent.mkdir(parents=True, exist_ok=True)
        sessions.append(str(self.logbase))
        if reset:
            self.build_xv6()
            self.reset_fs()
        q = ["make", "qemu", f"CPUS={config.cpus}"]
        self.launch = q
        self.control_dir = tempfile.TemporaryDirectory(prefix="xv6-qmp-") if control else None
        self.control_socket = None
        self.control_stream = None
        if control:
            self.control_path = os.path.join(self.control_dir.name, "qmp")
            q.append(f"QEMUEXTRA=-qmp unix:{self.control_path},server=on,wait=off")
        self.proc = subprocess.Popen(q, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT,
                                      start_new_session=True)
        os.set_blocking(self.proc.stdout.fileno(), False)
        self.output = ""
        self.outbytes = bytearray()
        self.reported = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            if exc[0] is not None:
                self.read()
                print(self.output)
        finally:
            self.stop()
            self.read()
            self.save_output()
            self.proc.stdin.close()
            self.proc.stdout.close()

    def reset_fs(self):
        if os.path.exists("fs.img"):
            os.unlink("fs.img")
        run(["make", "fs.img"], check=True)

    def build_xv6(self):
        run(["make", "kernel/kernel"], check=True)

    def save_output(self):
        Path("test-xv6.out").write_text(self.output)
        self.logbase.with_suffix(".serial.log").write_text(self.output)
        self.logbase.with_suffix(".json").write_text(json.dumps({
            "launch": self.launch, "commands": self.commands,
            "cpus": config.cpus, "commit": git_metadata("rev-parse", "HEAD"),
            "dirty": git_metadata("status", "--short"),
        }, indent=2))

    def cmd(self, c):
        if isinstance(c, str):
            c = c.encode('utf-8')
        self.commands.append(c.decode('utf-8', 'replace'))
        self.proc.stdin.write(c)
        self.proc.stdin.flush()
        
    def crash(self):
        if self.proc.poll() is not None:
            self.error("QEMU exited before crash")
        self.stop(signal.SIGKILL)

    def stop(self, sig=signal.SIGTERM):
        # make and QEMU share a private process group. Kill both, including
        # when make has already exited, and wait before reusing the image.
        try:
            os.killpg(self.proc.pid, sig)
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        if self.control_stream:
            self.control_stream.close()
            self.control_stream = None
        if self.control_socket:
            self.control_socket.close()
            self.control_socket = None
        if self.control_dir:
            self.control_dir.cleanup()
            self.control_dir = None

    def qmp(self, command):
        if self.control_socket is None:
            self.control_socket = socket.socket(socket.AF_UNIX)
            self.control_socket.settimeout(5)
            self.control_socket.connect(self.control_path)
            self.control_stream = self.control_socket.makefile("rb")
            greeting = json.loads(self.control_stream.readline())
            if "QMP" not in greeting:
                self.error("Invalid QMP greeting")
            self.qmp("qmp_capabilities")
        self.control_socket.sendall(json.dumps({"execute": command}).encode() + b"\n")
        while True:
            line = self.control_stream.readline()
            if not line:
                self.error("QMP disconnected")
            reply = json.loads(line)
            if "error" in reply:
                self.error(f"QMP {command}: {reply['error']}")
            if "return" in reply:
                return reply["return"]
            # STOP/RESUME events may arrive before the command response.

    def crash_in_log(self, timeout=30):
        # kernel/fs.h: little-endian superblock in block 1, logstart is
        # its sixth uint. Inspect the header only with guest CPUs stopped,
        # so they cannot clear the committed transaction before the kill.
        with open("fs.img", "rb", buffering=0) as disk:
            disk.seek(1024)
            magic, _, _, _, nlog, logstart, _, _ = struct.unpack("<8I", disk.read(32))
            if magic != 0x10203040:
                self.error("Invalid filesystem superblock")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self.qmp("stop")
                disk.seek(logstart * 1024)
                pending, = struct.unpack("<I", disk.read(4))
                if 0 < pending < nlog:
                    print(f"Crash with {pending} committed log blocks")
                    self.crash()
                    return
                self.qmp("cont")
                time.sleep(0.01)
                self.read()
                if re.search(r"panic:|failed|out of blocks", self.output):
                    self.error("logstress failed before crash")
            self.error("No committed log transaction observed")

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
        self.monitor(r'\$ *$', timeout=timeout)

    def error(self, *regexps):
        print("FAIL: match failed", regexps)
        raise RuntimeError(f"match failed: {regexps}")

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
        deadline = time.monotonic() + timeout
        while True:
            self.read()
            if progress:
                self.progress(progress)
            if self.match(*regexps, exit=False):
                return True
            if self.proc.poll() is not None:
                self.error("QEMU exited", *regexps)
            if time.monotonic() >= deadline:
                if fail:
                    self.error(*regexps)
                return False
            time.sleep(0.1)

def crash_log():
    with QEMU(True, control=True) as q:
        q.wait_shell()
        q.cmd("logstress f0 f1 f2 f3 f4 f5\n")
        q.monitor('^logstress ready$', timeout=30)
        q.crash_in_log()

def recover_log():
    with QEMU() as q:
        q.wait_shell()
        q.match('^recovering')
        q.cmd("ls\n")
        for i in range(6):
            q.monitor(rf'^f{i}\s+2\s+\d+\s+\d+\s*$', timeout=10)

def forphan():
    with QEMU(True) as q:
        q.wait_shell()
        q.cmd("forphan\n")
        q.monitor('wait', timeout=30)
        q.crash()

def dorphan():
    with QEMU(True) as q:
        q.wait_shell()
        q.cmd("dorphan\n")
        q.monitor('wait', timeout=30)
        q.crash()

def recover_orphan():
    with QEMU() as q:
        q.monitor('^ireclaim', timeout=30)
        q.wait_shell()

def test_log():
    print("Test recovery of log")
    crash_log()
    recover_log()
    print("OK")
    
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
    with QEMU(True) as q:
        q.wait_shell()
        q.cmd("usertests" + opt + "\n")
        q.monitor('^ALL TESTS PASSED', progress='test', timeout=timeout)

DEDICATED = ("cowtest", "kmemtest", "waitxtest", "cputest", "priotest", "mixstress")


def git_metadata(*command):
    result = run(["git", *command], capture_output=True, text=True)
    return result.stdout.strip()


def run_guest(q, command, timeout):
    start = len(q.output)
    q.cmd("testrun " + command + "\n")
    name = command.split()[0]
    deadline = time.monotonic() + timeout
    while True:
        q.read()
        output = q.output[start:]
        result = re.search(r"^TEST RESULT " + re.escape(name) + r" (-?\d+)\r?$", output, re.M)
        if re.search(r"panic:|FAIL|MISMATCH", output):
            raise RuntimeError(f"{command}: guest failure")
        if result:
            if int(result[1]) != 0:
                raise RuntimeError(f"{command}: exit status {result[1]}")
            if re.search(r"\$ *$", output[result.end():]):
                if not re.search(r"^" + re.escape(name) + r": OK\b", output, re.M):
                    raise RuntimeError(f"{command}: missing success marker")
                print(f"PASS {command}")
                return
        if q.proc.poll() is not None:
            raise RuntimeError(f"{command}: QEMU exited")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{command}: timeout after {timeout}s")
        time.sleep(0.05)


def main():
    global config
    config = args
    if args.repeat < 1 or args.timeout <= 0:
        parser.error("repeat and timeout must be positive")
    directory = Path(args.artifacts)
    directory.mkdir(parents=True, exist_ok=True)
    summary = {"cpus": args.cpus, "argv": sys.argv, "commit": git_metadata("rev-parse", "HEAD"),
               "dirty": git_metadata("status", "--short"), "results": [], "sessions": sessions}
    destination = directory / (str(time.time_ns()) + "-summary.json")
    if args.testrex == "dedicated":
        selected = list(DEDICATED)
    elif args.testrex in DEDICATED:
        selected = [args.testrex]
    else:
        funcs = {"usertests": test_usertests, "crash": test_crash,
                 "log": test_log, "forphan": test_forphan, "dorphan": test_dorphan}
        selected = [name for name in funcs if re.search(args.testrex, "test_" + name)]
        if args.testrex in funcs:
            selected = [args.testrex]
        if not selected:
            selected = ["usertests:" + args.testrex]
    try:
        for iteration in range(1, args.repeat + 1):
            for name in selected:
                row = {"test": name, "iteration": iteration, "status": "FAIL"}
                summary["results"].append(row)
                started = time.monotonic()
                print(f"RUN {name} CPU={args.cpus} iteration={iteration}")
                try:
                    if name in DEDICATED:
                        with QEMU(True) as q:
                            q.wait_shell()
                            run_guest(q, name, args.timeout)
                    elif name.startswith("usertests:"):
                        test_usertests(name.split(":", 1)[1])
                    else:
                        funcs[name]()
                    row["status"] = "PASS"
                except BaseException as exc:
                    row["error"] = str(exc)
                    raise
                finally:
                    row["seconds"] = round(time.monotonic() - started, 3)
    finally:
        destination.write_text(json.dumps(summary, indent=2))
        print(f"Summary: {destination}")
        for row in summary["results"]:
            print(f"{row['status']} {row['test']} #{row['iteration']} ({row['seconds']}s)")

if __name__ == "__main__":
    args = parser.parse_args()
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, KeyboardInterrupt) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
