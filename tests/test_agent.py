import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from sandbox.actions import returning_argv
from sandbox.agent import drive
from sandbox.backend import Backend, Result


class Local(Backend):
    def __init__(self):
        self.calls = []

    def exec(self, vm, argv, stdin=None):
        self.calls.append(argv)
        p = subprocess.run(argv, capture_output=True, timeout=20)
        return Result(p.returncode, p.stdout, p.stderr)


class Windows(Local):
    windows = True


class AgentTests(unittest.TestCase):
    def test_returns_when_main_process_exits_though_a_background_server_holds_its_output(self):
        started = time.monotonic()
        p = subprocess.run(returning_argv(["/bin/bash", "-lc", "nohup sleep 30 & echo started; echo oops >&2; exit 3"]),
                           capture_output=True, timeout=20)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((p.returncode, p.stdout, p.stderr), (3, b"started\n", b"oops\n"))

    def drive(self, backend):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "model.sh"
            script.write_text('if grep -q "^[$] "; then echo \'{"done": true}\'; else echo "echo hi"; fi\n')
            drive(backend, "vm", "task", f"sh {script}", log=lambda *_: None)

    def test_linux_commands_are_wrapped(self):
        b = Local()
        self.drive(b)
        self.assertEqual(b.calls, [returning_argv(["/bin/bash", "-lc", "echo hi"])])

    def test_windows_commands_are_not_wrapped(self):
        b = Windows()
        self.drive(b)
        self.assertEqual(b.calls, [["/bin/bash", "-lc", "echo hi"]])


if __name__ == "__main__":
    unittest.main()
