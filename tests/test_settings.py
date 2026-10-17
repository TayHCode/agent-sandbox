
from sandbox.hyperv import HyperV
from sandbox.incus import DEFAULT_IMAGE, Incus
from sandbox.backend import Result
from sandbox.tart import Tart
from tests.support import Case


class TestSettings(Case):

    def test_hyperv_requires_host(self):
        monkeypatch = self.monkeypatch
        monkeypatch.delenv("SANDBOX_HYPERV_HOST")
        with self.assertRaisesRegex(SystemExit, "SANDBOX_HYPERV_HOST"):
            HyperV()

    def test_hyperv_host_from_environment(self):
        monkeypatch = self.monkeypatch
        monkeypatch.setenv("SANDBOX_HYPERV_HOST", "me@winhost")
        assert HyperV().host == "me@winhost"

    def test_tart_runs_locally_without_host(self):
        calls = []
        backend = Tart(lambda argv, stdin=None: calls.append(argv) or Result(0, b"[]", b""))
        backend.list_owned()
        assert calls[0][:2] == ["sh", "-c"]

    def test_tart_uses_ssh_host(self):
        monkeypatch = self.monkeypatch
        monkeypatch.setenv("SANDBOX_TART_HOST", "me@machost")
        calls = []
        Tart(lambda argv, stdin=None: calls.append(argv) or Result(0, b"[]", b"")).list_owned()
        assert calls[0][:2] == ["ssh", "me@machost"]

    def test_incus_storage_pool_is_optional(self):
        monkeypatch = self.monkeypatch
        calls = []

        def runner(argv, stdin=None):
            calls.append(argv)
            return Result(0, b"[]", b"")

        monkeypatch.setenv("SANDBOX_INCUS_STORAGE", "fast")
        Incus(runner).create("sbx-a", None, 1, 1024)
        init = next(c for c in calls if "init" in c)
        assert init[init.index("--storage") + 1] == "fast"
