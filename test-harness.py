#!/usr/bin/env python3
"""Regression tests for the host-side harness; no guest/toolchain required."""

import importlib.util
from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
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


if __name__ == "__main__":
    unittest.main()
