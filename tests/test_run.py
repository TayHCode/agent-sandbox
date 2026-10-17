import json

from sandbox.actions import parse_action
from sandbox.backend import Backend, Result, created_at
from sandbox.run import gc, run
from tests.support import Case


class Fake(Backend):

    def __init__(self):
        self.vms = {}

    def create(self, vm, image, cpus, memory_mb):
        self.vms[vm] = {}

    def exec(self, vm, argv, stdin=None):
        return Result(3, b"out", b"err")

    def put(self, vm, local, remote):
        self.vms[vm][remote] = local

    def get(self, vm, remote, local):
        pass

    def destroy(self, vm):
        self.vms.pop(vm, None)

    def list_owned(self):
        return list(self.vms)


class TestRun(Case):

    def test_run_cleans_up_and_writes_result(self):
        b = Fake()
        r = run(b, lambda b, vm: b.exec(vm, ["x"]), result_dir=self.tmp_path)
        assert r["returncode"] == 3 and r["cleaned"] and not b.vms
        assert json.loads((self.tmp_path / "result.json").read_text())["cleaned"] is True
        assert (self.tmp_path / "stdout").read_bytes() == b"out"

    def test_cleanup_on_failure(self):
        b = Fake()

        def boom(b, vm):
            raise RuntimeError("x")

        try:
            run(b, boom)
        except RuntimeError:
            pass
        assert not b.vms

    def test_gc_removes_old_only(self):
        b = Fake()
        b.vms.update({"sbx-100-aaaaaa": {}, "sbx-99999999999-bbbbbb": {}})
        assert gc(b, 1) == ["sbx-100-aaaaaa"] and created_at("sbx-100-aaaaaa") == 100

    def test_parse_action(self):
        assert parse_action("done") is None
        assert parse_action('{"command": "ls"}')[-1] == "ls"
        assert parse_action("```bash\nls\n```")[-1] == "ls"
        assert parse_action({"done": True}) is None
