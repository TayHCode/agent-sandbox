import subprocess
import sys
from pathlib import Path

from tests.support import Case


class TestCliEntry(Case):

    def test_python_dash_m_sandbox_cli_runs_main(self):
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run([sys.executable, "-m", "sandbox.cli", "--help"], cwd=root, capture_output=True, text=True)
        assert result.returncode == 0
        assert "usage" in result.stdout.lower()
