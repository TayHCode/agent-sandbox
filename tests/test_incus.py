from sandbox.backend import Result
from sandbox.incus import DEFAULT_IMAGE, Incus
from tests.support import Case


class Runner:

    def __init__(self, probe_rc=0):
        self.calls = []
        self.probe_rc = probe_rc

    def __call__(self, argv, stdin=None):
        self.calls.append((argv, stdin))
        if argv[-3:] == ["list", "--format", "json"]:
            if len(self.calls) == 1:
                return Result(self.probe_rc, b"[]", b"")
            return Result(0, b'[{"name":"sbx-a"},{"name":"other"}]', b"")
        if argv[-2:] == ["true"]:
            return Result(0, b"", b"")
        if argv[-1:] == ["cmd"]:
            return Result(7, b"out", b"err")
        return Result(0, b"", b"")


class TestIncus(Case):

    def test_create_uses_default_image_and_waits_for_agent(self):
        runner = Runner()
        incus = Incus(runner)
        incus.create("sbx-a", None, 3, 2048)
        assert runner.calls[1][0] == ["incus", "init", DEFAULT_IMAGE, "sbx-a", "--vm", "-c", "limits.cpu=3", "-c",
                                      "limits.memory=2048MiB"]
        assert runner.calls[2][0] == ["incus", "start", "sbx-a"]
        assert runner.calls[3][0][-3:] == ["sbx-a", "--", "true"]

    def test_uses_sudo_when_unaided_probe_fails_and_preserves_exec_result_and_stdin(self):
        runner = Runner(probe_rc=1)
        incus = Incus(runner)
        result = incus.exec("sbx-a", ["cmd"], b"input")
        assert runner.calls[0][0][:2] == ["incus", "list"]
        assert runner.calls[1] == (["sudo", "-n", "incus", "exec", "sbx-a", "--", "cmd"], b"input")
        assert (result.returncode, result.stdout, result.stderr) == (7, b"out", b"err")

    def test_file_transfer_delete_and_owned_filter(self):
        runner = Runner()
        incus = Incus(runner)
        incus.put("sbx-a", "/tmp/in", "/workspace/in")
        incus.get("sbx-a", "/workspace/out", "/tmp/out")
        incus.destroy("sbx-a")
        assert incus.list_owned() == ["sbx-a"]
        assert [call[0][1:] for call in runner.calls[1:4]] == [
            ["file", "push", "-p", "/tmp/in", "sbx-a/workspace/in"],
            ["file", "pull", "sbx-a/workspace/out", "/tmp/out"],
            ["delete", "sbx-a", "--force"],
        ]
