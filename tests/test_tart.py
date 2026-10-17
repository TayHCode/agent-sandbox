import json
import io
import tarfile

from sandbox.backend import Result
from sandbox.tart import DEFAULT_MACOS_IMAGE, Tart
from tests.support import Case


class Runner:

    def __init__(self):
        self.calls = []

    def __call__(self, argv, stdin=None):
        self.calls.append((argv, stdin))
        command = argv[-1]
        if "tart list" in command:
            return Result(0, json.dumps([{"Name": "sbx-1-abcdef"}, {"Name": "base"}]).encode(), b"")
        if "-cf -" in command:
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                data = b"hello"
                item = tarfile.TarInfo("source.txt")
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            return Result(0, stream.getvalue(), b"")
        return Result(0, b"", b"")


class TestTart(Case):

    def test_create_exec_and_destroy(self):
        runner = Runner()
        backend = Tart(runner)
        backend.create("sbx-1-abcdef", None, 3, 2048)
        result = backend.exec("sbx-1-abcdef", ["uname", "-a"], b"input")
        backend.destroy("sbx-1-abcdef")
        commands = [call[0][-1] for call in runner.calls]
        assert any("tart clone ghcr.io/cirruslabs/ubuntu:latest sbx-1-abcdef" in c for c in commands)
        assert any("tart set sbx-1-abcdef --cpu 3 --memory 2048" in c for c in commands)
        assert any("tart exec -i sbx-1-abcdef sh -c" in c for c in commands)
        assert any("tart delete sbx-1-abcdef" in c for c in commands)
        assert result.returncode == 0

    def _test_put_get_and_list_owned(self, os_name_):
        os_name = os_name_
        runner = Runner()
        backend = Tart(runner, os_name=os_name)
        source = self.tmp_path / "source.txt"
        source.write_text("hello")
        backend.put("sbx-1-abcdef", str(source), "/tmp/source.txt")
        output = self.tmp_path / "output"
        output.mkdir()
        backend.get("sbx-1-abcdef", "/tmp/source.txt", str(output / "source.txt"))
        assert (output / "source.txt").read_text() == "hello"
        assert backend.list_owned() == ["sbx-1-abcdef"]
        assert any(stdin for _, stdin in runner.calls)

    def test_put_get_and_list_owned_linux(self):
        self._test_put_get_and_list_owned('linux')

    def test_put_get_and_list_owned_macos(self):
        self._test_put_get_and_list_owned('macos')

    def _test_exec_preserves_nonzero_exit(self, os_name_):
        os_name = os_name_
        runner = Runner()
        runner.returncode = 7
        def fail(argv, stdin=None):
            runner.calls.append((argv, stdin))
            return Result(7, b"out", b"err")
        backend = Tart(fail, os_name=os_name)
        result = backend.exec("sbx-1-abcdef", ["sh", "-c", "exit 7"])
        assert result.returncode == 7
        assert result.stdout == b"out" and result.stderr == b"err"

    def test_exec_preserves_nonzero_exit_linux(self):
        self._test_exec_preserves_nonzero_exit('linux')

    def test_exec_preserves_nonzero_exit_macos(self):
        self._test_exec_preserves_nonzero_exit('macos')

    def _test_macos_create_waits_for_agent_and_uses_home_shell(self, image_):
        monkeypatch = self.monkeypatch
        image = image_
        runner = Runner()
        attempts = []

        def booting(argv, stdin=None):
            result = runner(argv, stdin)
            if "tart exec sbx-1-abcdef true" in argv[-1]:
                attempts.append(argv)
                if len(attempts) == 1:
                    return Result(1, b"", b"guest agent not ready")
            return result

        monkeypatch.setattr("sandbox.tart.time.sleep", lambda seconds: None)
        backend = Tart(booting, os_name="macos")
        backend.create("sbx-1-abcdef", image)
        backend.exec("sbx-1-abcdef", ["cat", "file with spaces"], b"hello")
        backend.destroy("sbx-1-abcdef")
        commands = [argv[-1] for argv, _ in runner.calls]
        assert f"tart clone {image or DEFAULT_MACOS_IMAGE} sbx-1-abcdef" in commands[0]
        assert len(attempts) == 2
        assert "tart exec -i sbx-1-abcdef /bin/sh -c" in commands[-3]
        assert "cd \"$HOME\" && exec \"$@\"" in commands[-3]
        assert "'file with spaces'" in commands[-3]
        assert runner.calls[-3][1] == b"hello"
        assert commands[-2].endswith("tart stop sbx-1-abcdef")
        assert commands[-1].endswith("tart delete sbx-1-abcdef")

    def test_macos_create_waits_for_agent_and_uses_home_shell_None(self):
        self._test_macos_create_waits_for_agent_and_uses_home_shell(None)

    def test_macos_create_waits_for_agent_and_uses_home_shell_custom_macos(self):
        self._test_macos_create_waits_for_agent_and_uses_home_shell('custom-macos')
