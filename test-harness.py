#!/usr/bin/env python3
"""Regression tests for the host-side harness; no guest/toolchain required."""

import importlib.util
from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import tempfile
import os
import sys
import json
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "xv6_harness", Path(__file__).with_name("test-xv6.py"))
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.q = harness.QEMU.__new__(harness.QEMU)
        self.q.output = ""
        self.q.read = Mock()
        self.q.proc = Mock()
        self.q.proc.poll.return_value = None

    def test_shell_timeout_is_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "match failed"):
            self.q.wait_shell(timeout=0)

    def test_last_read_is_checked_before_timeout(self):
        self.q.read.side_effect = lambda: setattr(self.q, "output", "$ ")
        self.q.wait_shell(timeout=0)

    def test_early_exit_is_not_silently_retried(self):
        self.q.proc.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "QEMU exited"):
            self.q.monitor("ready", timeout=30, fail=False)

    def test_build_failure_propagates(self):
        with patch.object(harness, "run", side_effect=subprocess.CalledProcessError(2, "make")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.q.build_xv6()

    def test_qmp_events_do_not_count_as_responses(self):
        self.q.control_socket = Mock()
        self.q.control_stream = io.BytesIO(b'{"event":"STOP"}\n{"return":{}}\n')
        self.assertEqual(self.q.qmp("stop"), {})
        self.assertEqual(self.q.control_stream.read(), b"")

    def test_qmp_error_is_an_error(self):
        self.q.control_socket = Mock()
        self.q.control_stream = io.BytesIO(b'{"error":{"desc":"bad command"}}\n')
        with self.assertRaisesRegex(RuntimeError, "bad command"):
            self.q.qmp("stop")

    def test_qmp_disconnect_is_an_error(self):
        self.q.control_socket = Mock()
        self.q.control_stream = io.BytesIO(b"")
        with self.assertRaisesRegex(RuntimeError, "QMP disconnected"):
            self.q.qmp("stop")

    def test_context_cleans_up_and_saves_failure_output(self):
        self.q.stop = Mock()
        self.q.save_output = Mock()
        with self.assertRaisesRegex(RuntimeError, "guest failed"):
            with self.q:
                raise RuntimeError("guest failed")
        self.q.stop.assert_called_once()
        self.q.save_output.assert_called_once()
        self.q.proc.stdin.close.assert_called_once()
        self.q.proc.stdout.close.assert_called_once()


class DedicatedTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.q = Mock()
        self.q.output = "$ "
        self.q.proc.poll.return_value = None

    def deliver(self, output):
        self.q.read.side_effect = lambda: setattr(self.q, "output", "$ " + output)

    def test_success_requires_exit_status_and_prompt(self):
        self.deliver("cowtest: OK\nTEST RESULT cowtest 0\n$ ")
        harness.run_guest(self.q, "cowtest", 0)
        self.q.cmd.assert_called_once_with("testrun cowtest\n")

    def test_nonzero_exit_overrides_ok(self):
        self.deliver("cowtest: OK\nTEST RESULT cowtest 1\n$ ")
        with self.assertRaisesRegex(RuntimeError, "exit status 1"):
            harness.run_guest(self.q, "cowtest", 0)

    def test_hang_is_failure(self):
        self.deliver("cowtest started\n")
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            harness.run_guest(self.q, "cowtest", 0)

    def test_ok_without_prompt_is_failure(self):
        self.deliver("cowtest: OK\nTEST RESULT cowtest 0\n")
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            harness.run_guest(self.q, "cowtest", 0)

    def test_stale_success_is_not_reused(self):
        self.q.output = "cowtest: OK\nTEST RESULT cowtest 0\n$ "
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            harness.run_guest(self.q, "cowtest", 0)

    def test_missing_success_is_failure(self):
        self.deliver("TEST RESULT cowtest 0\n$ ")
        with self.assertRaisesRegex(RuntimeError, "missing success"):
            harness.run_guest(self.q, "cowtest", 0)

    def test_failure_diagnostic_overrides_zero_exit(self):
        for diagnostic in ("FAIL", "FAILED", "MISMATCH", "panic: broken"):
            with self.subTest(diagnostic=diagnostic):
                self.q.output = "$ "
                self.deliver(diagnostic + "\nTEST RESULT cowtest 0\n$ ")
                with self.assertRaisesRegex(RuntimeError, "guest failure"):
                    harness.run_guest(self.q, "cowtest", 0)

    def test_early_qemu_exit_is_failure(self):
        self.q.proc.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "QEMU exited"):
            harness.run_guest(self.q, "cowtest", 10)

    def test_cpu_configuration_and_failure_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            config = harness.argparse.Namespace(cpus=1, artifacts=directory)
            with patch.object(harness, "config", config), \
                 patch.object(harness.subprocess, "Popen") as popen, \
                 patch.object(harness.os, "set_blocking"), \
                 patch.object(harness.QEMU, "read"), \
                 patch.object(harness.QEMU, "stop") as stop, \
                 patch.object(harness, "git_metadata", return_value="revision"):
                q = harness.QEMU()
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    with q:
                        q.cmd("testrun cowtest\n")
                        q.output = "cowtest: FAIL injected\n"
                        raise RuntimeError("injected")
                self.assertIn("CPUS=1", popen.call_args.args[0])
                stop.assert_called_once()
                data = json.loads(q.logbase.with_suffix(".json").read_text())
                self.assertEqual(data["cpus"], 1)
                self.assertEqual(data["commands"], ["testrun cowtest\n"])
                self.assertEqual(data["commit"], "revision")
                self.assertIn("FAIL injected", q.logbase.with_suffix(".serial.log").read_text())

    def test_failure_stops_repeats_and_writes_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            args = harness.argparse.Namespace(cpus=3, artifacts=directory,
                repeat=10, timeout=1, testrex="cowtest")
            with patch.object(harness, "args", args, create=True), \
                 patch.object(harness, "QEMU"), \
                 patch.object(harness, "run_guest", side_effect=RuntimeError("injected hang")) as guest, \
                 patch.object(harness, "git_metadata", return_value="revision"):
                with self.assertRaisesRegex(RuntimeError, "injected hang"):
                    harness.main()
                guest.assert_called_once()
                summary = json.loads(next(Path(directory).glob("*-summary.json")).read_text())
                self.assertEqual(len(summary["results"]), 1)
                self.assertEqual(summary["results"][0]["status"], "FAIL")
                self.assertIn("injected hang", summary["results"][0]["error"])


class ProcessTests(unittest.TestCase):
    def test_cli_failure_and_hang_leave_logs_and_no_process(self):
        # A real child process substitutes for make/QEMU. Exercise the CLI,
        # process group cleanup and disk artifacts without a cross toolchain.
        for mode in ("failure", "hang"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fake = root / "make"
                fake.write_text("#!" + sys.executable + "\n" + """
import os, sys, time
from pathlib import Path
if 'qemu' not in sys.argv:
    sys.exit(0)
Path('guest.pid').write_text(str(os.getpid()))
print('$ ', end='', flush=True)
sys.stdin.readline()
print('cowtest: OK', flush=True)
if os.environ['INJECT_MODE'] == 'failure':
    print('TEST RESULT cowtest 1\\n$ ', end='', flush=True)
while True:
    time.sleep(1)
""")
                fake.chmod(0o755)
                env = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"],
                           INJECT_MODE=mode)
                result = subprocess.run([sys.executable,
                    str(Path(__file__).with_name("test-xv6.py").resolve()),
                    "cowtest", "--cpus", "3", "--timeout", "0.2"],
                    cwd=directory, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                reason = "exit status 1" if mode == "failure" else "timeout"
                self.assertIn(reason, result.stderr)
                summary = json.loads(next((root / "test-results").glob("*-summary.json")).read_text())
                self.assertEqual(summary["results"][0]["status"], "FAIL")
                self.assertTrue(list((root / "test-results").glob("*.serial.log")))
                with self.assertRaises(ProcessLookupError):
                    os.kill(int((root / "guest.pid").read_text()), 0)


if __name__ == "__main__":
    unittest.main()
