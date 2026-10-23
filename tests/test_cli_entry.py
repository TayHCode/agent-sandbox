import subprocess
import sys
import unittest
from pathlib import Path

from tests.support import Case


class TestCliEntry(Case):

    def test_python_dash_m_sandbox_cli_runs_main(self):
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run([sys.executable, "-m", "sandbox.cli", "--help"], cwd=root, capture_output=True, text=True)
        assert result.returncode == 0
        assert "usage" in result.stdout.lower()


class UnsupportedControllerTests(unittest.TestCase):
    def test_windows_refuses_before_importing_posix_modules(self):
        root = Path(__file__).resolve().parent.parent
        script = (
            "import runpy, sys; sys.platform = 'win32'; "
            "sys.modules['pwd'] = None; sys.modules['fcntl'] = None; "
            "runpy.run_module(sys.argv.pop(1), run_name='__main__')"
        )
        for module in ("sandbox", "sandbox.cli"):
            for args in (
                ["--help"],
                ["run", "--backend", "hyperv", "--", "whoami"],
                ["agent", "--backend", "hyperv", "--task", "test", "--model-cmd", "echo"],
                ["gc", "--backend", "hyperv"],
            ):
                with self.subTest(module=module, args=args):
                    result = subprocess.run(
                        [sys.executable, "-c", script, module, *args],
                        cwd=root, capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("Linux, macOS, or WSL", result.stderr)
                    self.assertIn("native Windows is not supported", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
