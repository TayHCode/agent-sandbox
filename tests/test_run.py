import json
import os
import signal
import subprocess
import sys

from sandbox.actions import parse_action
from sandbox.backend import Backend, Result
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

    def test_gc_reaps_orphans_and_skips_unowned_unless_asked(self):
        b = Fake()
        leases = self.tmp_path / "leases"
        leases.mkdir()
        (leases / "sbx-100-aaaaaa").write_text("{}")
        b.vms.update({"sbx-100-aaaaaa": {}, "sbx-100-bbbbbb": {}, "base-image": {}})
        assert gc(b, dry_run=True) == ["sbx-100-aaaaaa"] and len(b.vms) == 3
        assert gc(b) == ["sbx-100-aaaaaa"]
        assert sorted(b.vms) == ["base-image", "sbx-100-bbbbbb"]
        assert gc(b, include_unowned=True) == ["sbx-100-bbbbbb"]
        assert list(b.vms) == ["base-image"]

    def test_gc_reaps_old_leftover_files_without_a_vm_but_not_young_or_leased_ones(self):
        b = Fake()
        b.leftovers = {"sbx-100-cccccc": 1, "sbx-100-dddddd": 1, "sbx-99999999999-eeeeee": 1, "base-image": 1}
        b.list_leftovers = lambda: list(b.leftovers)
        b.destroy = lambda vm: b.leftovers.pop(vm)
        leases = self.tmp_path / "leases"
        leases.mkdir()
        held = os.open(leases / "sbx-100-dddddd", os.O_CREAT | os.O_RDWR)
        self.addCleanup(os.close, held)
        import fcntl
        fcntl.flock(held, fcntl.LOCK_EX)
        assert gc(b, dry_run=True) == ["sbx-100-cccccc"]
        assert gc(b) == ["sbx-100-cccccc"]
        assert sorted(b.leftovers) == ["base-image", "sbx-100-dddddd", "sbx-99999999999-eeeeee"]

    def test_gc_reaps_killed_controller_but_not_live_one(self):
        holder = (
            "import sys,time\n"
            "from tests.test_run import Fake\n"
            "b = Fake()\n"
            "b.create(sys.argv[1], None, 1, 1)\n"
            "print('held', flush=True); time.sleep(60)\n"
        )
        live, orphan = "sbx-100-aaaaaa", "sbx-100-bbbbbb"
        procs = [subprocess.Popen([sys.executable, "-c", holder, vm], stdout=subprocess.PIPE, text=True)
                 for vm in (live, orphan)]
        self.addCleanup(lambda: [p.kill() or p.wait() for p in procs])
        for p in procs:
            assert p.stdout.readline() == "held\n"
        procs[1].send_signal(signal.SIGKILL)
        procs[1].wait()
        b = Fake()
        b.vms.update({live: {}, orphan: {}, "sbx-100-cccccc": {}})
        assert gc(b) == [orphan]
        assert sorted(b.vms) == [live, "sbx-100-cccccc"]
        assert sorted(p.name for p in (self.tmp_path / "leases").iterdir()) == [live]

    def test_sigterm_destroys_the_vm_before_exit(self):
        script = (
            "import sys,time\n"
            "from tests.test_run import Fake\n"
            "b = Fake()\n"
            "b.create('sbx-100-dddddd', None, 1, 1)\n"
            "real = b.destroy\n"
            "b.destroy = lambda vm: (open(sys.argv[1], 'w').write(vm), real(vm))\n"
            "print('held', flush=True); time.sleep(60)\n"
        )
        marker = self.tmp_path / "destroyed"
        p = subprocess.Popen([sys.executable, "-c", script, str(marker)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: p.kill() or p.wait())
        assert p.stdout.readline() == "held\n"
        p.send_signal(signal.SIGTERM)
        p.wait(10)
        assert marker.read_text() == "sbx-100-dddddd"
        assert not list((self.tmp_path / "leases").iterdir())

    def test_failed_destroy_keeps_the_lease_so_the_reaper_can_retry(self):
        class Broken(Fake):
            def destroy(self, vm):
                raise RuntimeError("no")

        b = Broken()
        b.create("sbx-100-eeeeee", None, 1, 1)
        with self.assertRaises(RuntimeError):
            b.destroy("sbx-100-eeeeee")
        from sandbox import lease
        lease.HELD.pop("sbx-100-eeeeee")
        assert (self.tmp_path / "leases" / "sbx-100-eeeeee").exists()

    def test_lease_released_after_run(self):
        b = Fake()
        run(b, lambda b, vm: b.exec(vm, ["x"]))
        assert not list((self.tmp_path / "leases").iterdir())

    def test_parse_action(self):
        assert parse_action("done") is None
        assert parse_action('{"command": "ls"}')[-1] == "ls"
        assert parse_action("```bash\nls\n```")[-1] == "ls"
        assert parse_action({"done": True}) is None
