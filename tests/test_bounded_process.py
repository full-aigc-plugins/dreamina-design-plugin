"""Exercise real pipe pressure, process groups, and exceptional cleanup."""
import os
import selectors
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.bounded_process import BoundedProcessError, BoundedProcessOutput, BoundedProcessTimeout, run_bounded


def run(code, **options):
    return run_bounded([sys.executable, "-I", "-c", code], env={"PATH": "/usr/bin:/bin"},
                       **dict(timeout_seconds=2, stdout_cap=1024, stderr_cap=1024, **options))


class BoundedProcessTests(unittest.TestCase):
    def test_stdout_overflow_is_detected_before_timeout(self):
        with self.assertRaises(BoundedProcessOutput):
            run("import os,time; os.write(1,b'x'*65536); time.sleep(20)")

    def test_stderr_overflow_is_detected_before_timeout(self):
        with self.assertRaises(BoundedProcessOutput):
            run("import os,time; os.write(2,b'x'*65536); time.sleep(20)")

    def test_cap_is_bytes_not_unicode_characters(self):
        with self.assertRaises(BoundedProcessOutput):
            run("import os; os.write(1, '雪'.encode()*500)")

    def test_exact_caps_and_nonzero_status_are_preserved(self):
        result = run("import os; os.write(1,b'a'*1024); os.write(2,b'b'*1024); raise SystemExit(7)")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, "a"*1024, "b"*1024))

    def test_both_pipes_are_drained_without_deadlock(self):
        result = run("import os; [(os.write(1,b'a'), os.write(2,b'b')) for _ in range(1000)]")
        self.assertEqual((len(result.stdout), len(result.stderr)), (1000, 1000))

    def test_non_utf8_output_is_bounded_and_decoded(self):
        self.assertEqual(run("import os; os.write(1,b'\\xff')").stdout, "\ufffd")

    def test_eof_does_not_disable_deadline(self):
        with self.assertRaises(BoundedProcessTimeout):
            run("import os,time; os.close(1); os.close(2); time.sleep(20)")

    def test_descendant_is_killed_when_leader_exits_but_pipes_remain(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "escaped"
            code = f"import os,time; pid=os.fork();\nif pid: os._exit(0)\ntime.sleep(3); open({str(marker)!r},'w').write('escaped')"
            with self.assertRaises(BoundedProcessTimeout):
                run(code)
            time.sleep(1.2)
            self.assertFalse(marker.exists(), "forked child survived timeout")

    def test_overflow_kills_child_after_leader_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "escaped"
            code = f"import os,time; pid=os.fork();\nif pid: os._exit(0)\nos.write(1,b'x'*65536); time.sleep(1); open({str(marker)!r},'w').write('escaped')"
            with self.assertRaises(BoundedProcessOutput):
                run(code)
            time.sleep(1.2)
            self.assertFalse(marker.exists())

    def test_selector_exception_closes_pipes_and_terminates_child(self):
        import subprocess
        original = subprocess.Popen
        children = []
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs); children.append(child); return child
        with patch("scripts.bounded_process.subprocess.Popen", side_effect=spawn), patch.object(
            selectors.DefaultSelector, "register", side_effect=RuntimeError("injected register failure")
        ), self.assertRaisesRegex(RuntimeError, "register failure"):
            run("import time; time.sleep(20)")
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed and children[0].stderr.closed)

    def test_invalid_limits_fail_before_spawning(self):
        for field, value in (("timeout_seconds", 0), ("timeout_seconds", float("nan")),
                             ("timeout_seconds", float("inf")), ("stdout_cap", -1),
                             ("stderr_cap", True), ("stdout_cap", 1.5)):
            options = dict(env={}, timeout_seconds=1, stdout_cap=10, stderr_cap=10)
            options[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(BoundedProcessError):
                run_bounded([sys.executable, "-c", "pass"], **options)

    def test_invalid_argv_fails_closed(self):
        for argv in ([], "echo", [1], ["/bin/echo", "a\0b"]):
            with self.subTest(argv=argv), self.assertRaises(BoundedProcessError):
                run_bounded(argv, env={}, timeout_seconds=1, stdout_cap=10, stderr_cap=10)

    def test_passed_fd_stays_caller_owned(self):
        with tempfile.TemporaryFile() as handle:
            result = run_bounded([sys.executable, "-c", f"import os; print(os.fstat({handle.fileno()}).st_size)"],
                env={}, timeout_seconds=2, stdout_cap=10, stderr_cap=10, pass_fds=[handle.fileno()])
            self.assertEqual(result.stdout, "0\n")
            os.fstat(handle.fileno())

    def test_no_shell_or_ambient_environment(self):
        result = run("import os; print(os.environ.get('REELBENCH_SECRET','absent'))")
        self.assertEqual(result.stdout, "absent\n")
