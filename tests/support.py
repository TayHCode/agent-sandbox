import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class Monkeypatch:
    def __init__(self, case):
        self.case = case
        self.env = None

    def setattr(self, target, value, *rest):
        if rest:
            patcher = mock.patch.object(target, value, rest[0])
        else:
            patcher = mock.patch(target, value)
        patcher.start()
        self.case.addCleanup(patcher.stop)

    def _environ(self):
        if self.env is None:
            self.env = mock.patch.dict(os.environ)
            self.env.start()
            self.case.addCleanup(self.env.stop)

    def setenv(self, name, value):
        self._environ()
        os.environ[name] = value

    def delenv(self, name, raising=True):
        self._environ()
        if raising:
            del os.environ[name]
        else:
            os.environ.pop(name, None)


class Case(unittest.TestCase):
    def setUp(self):
        self.monkeypatch = Monkeypatch(self)
        self.monkeypatch.setenv("SANDBOX_HYPERV_HOST", "hyperv-host")
        self.monkeypatch.delenv("SANDBOX_TART_HOST", raising=False)
        self.monkeypatch.delenv("SANDBOX_INCUS_STORAGE", raising=False)
        self.tmp_path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_path, ignore_errors=True)
