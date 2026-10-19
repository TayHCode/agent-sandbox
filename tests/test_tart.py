from unittest.mock import patch

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
        assert any("tart set sbx-1-abcdef --cpu 3 --memory 8192" in c for c in commands)
        assert any("tart exec -i sbx-1-abcdef sh -c" in c for c in commands)
        assert any("tart delete sbx-1-abcdef" in c for c in commands)
        assert result.returncode == 0

    def test_guest_memory_never_below_floor_but_larger_requests_are_kept(self):
        for asked, expected in ((2048, 8192), (4096, 8192), (16384, 16384)):
            runner = Runner()
            Tart(runner).create("sbx-1-abcdef", None, 2, asked)
            commands = [call[0][-1] for call in runner.calls]
            assert any(f"--memory {expected}" in c for c in commands)

    def _test_put_get_and_list_owned(self, os_name_):
        os_name = os_name_
        runner = Runner()
        backend = Tart(runner, os_name=os_name)
        source = self.tmp_path / "source.txt"
        source.write_text("hello")
        backend.put("sbx-1-abcdef", str(source), "/tmp/source.txt")
        output = self.tmp_path / "output"
        output.mkdir()
        backend.get("sbx-1-abcdef", "/tmp/source.txt", str(output / "renamed.txt"))
        assert (output / "renamed.txt").read_text() == "hello"
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
        assert commands[-2].endswith("tart stop sbx-1-abcdef --timeout 0")
        assert commands[-1].endswith("tart delete sbx-1-abcdef")

    def test_macos_create_waits_for_agent_and_uses_home_shell_None(self):
        self._test_macos_create_waits_for_agent_and_uses_home_shell(None)

    def test_macos_create_waits_for_agent_and_uses_home_shell_custom_macos(self):
        self._test_macos_create_waits_for_agent_and_uses_home_shell('custom-macos')


class TestTartHost(Case):

    def test_remote_ssh_calls_share_one_connection(self):
        self.monkeypatch.setenv("SANDBOX_TART_HOST", "user@mac-host")
        self.monkeypatch.setenv("HOME", str(self.tmp_path))
        argv = Tart(Runner()).remote
        assert argv[0] == "ssh" and argv[-1] == "user@mac-host"
        options = " ".join(argv)
        assert "ControlMaster=auto" in options and "ControlPersist=" in options
        assert f"ControlPath={self.tmp_path}/.ssh/sandbox-mux/%C" in options

    def test_local_tart_has_no_ssh_options(self):
        assert Tart(Runner()).remote == ["sh", "-c"]

    def test_vms_take_a_slot_from_the_tart_host_pool(self):
        self.monkeypatch.setenv("HOME", str(self.tmp_path))
        self.monkeypatch.setenv("SANDBOX_TART_HOST", "user@mac-host")
        assert Tart(Runner()).pool == "mac-host"
        self.monkeypatch.setenv("SANDBOX_TART_HOST", "mac-host")
        assert Tart(Runner()).pool == "mac-host"
        self.monkeypatch.delenv("SANDBOX_TART_HOST")
        assert Tart(Runner()).pool is None

    def test_destroy_is_hard_stop_and_tolerates_a_vm_that_is_already_gone(self):
        calls = []

        def runner(argv, stdin=None):
            calls.append(argv[-1])
            if "tart delete" in argv[-1]:
                return Result(2, b"", b'the specified VM "sbx-1-abcdef" does not exist')
            return Result(0, b"", b"")
        Tart(runner).destroy("sbx-1-abcdef")
        assert calls[0].endswith("tart stop sbx-1-abcdef --timeout 0")

    def test_destroy_still_raises_when_delete_really_fails(self):
        def runner(argv, stdin=None):
            return Result(1 if "tart delete" in argv[-1] else 0, b"", b"disk busy")
        with self.assertRaises(RuntimeError):
            Tart(runner).destroy("sbx-1-abcdef")


class TestTartStoppedGuest(Case):

    def _backend(self, always_stopped):
        calls = []
        state = {"runs": 0, "execs": 0}

        def runner(argv, stdin=None):
            command = argv[-1]
            calls.append(command)
            if "tart run" in command:
                state["runs"] += 1
            if "tart list" in command:
                running = state["runs"] >= 2 and not always_stopped
                return Result(0, json.dumps([{"Name": "sbx-1-abcdef", "State": "Running" if running else "Stopped"}]).encode(), b"")
            if "tart exec" in command:
                state["execs"] += 1
                return Result(0 if state["runs"] >= 2 and not always_stopped else 1, b"", b"")
            return Result(0, b"", b"")
        return Tart(runner), state

    def test_guest_that_stopped_before_answering_is_started_again(self):
        backend, state = self._backend(False)
        with patch("sandbox.tart.time.sleep"):
            backend.create("sbx-1-abcdef", None, 2, 4096)
        assert state["runs"] == 2

    def test_guest_that_stops_twice_fails_instead_of_waiting_forever(self):
        backend, state = self._backend(True)
        with patch("sandbox.tart.time.sleep"), self.assertRaises(RuntimeError):
            backend.create("sbx-1-abcdef", None, 2, 4096)
        assert state["runs"] == 2


class TestTartReadyProbe(Case):

    def test_readiness_probe_is_killed_after_a_short_alarm(self):
        runner = Runner()
        Tart(runner).create("sbx-1-abcdef", None, 2, 4096)
        probes = [call[0][-1] for call in runner.calls if "tart exec sbx-1-abcdef true" in call[0][-1]]
        assert len(probes) == 1
        assert "perl -e 'alarm 2; exec @ARGV' tart exec sbx-1-abcdef true" in probes[0]


class TestTartQueuesBehindHeldSlot(Case):

    def test_create_waits_while_the_only_mac_slot_is_held_and_proceeds_after_release(self):
        import os
        import threading
        import time
        from pathlib import Path
        from sandbox import lease

        self.monkeypatch.setattr(lease, "SLOT_ROOT", Path(self.tmp_path))
        self.monkeypatch.setenv("SANDBOX_LEASE_DIR", str(self.tmp_path / "leases"))
        self.monkeypatch.setenv("SANDBOX_TART_HOST", "mac-host")
        lease.publish_host_limit("mac-host", 1)
        held = lease.acquire_host_slot("mac-host")
        runner = Runner()
        done = threading.Event()

        def create():
            Tart(runner).create("sbx-1-abcdef", None, 2, 4096)
            done.set()

        thread = threading.Thread(target=create)
        thread.start()
        time.sleep(0.5)
        assert not done.is_set()
        assert runner.calls == []
        os.close(held)
        thread.join(10)
        assert done.is_set()
        assert any("tart clone" in call[0][-1] for call in runner.calls)


class TestTartGuestCpus(Case):

    def _runner(self, perf_cores):
        runner = Runner()
        base = runner.__call__

        def call(argv, stdin=None):
            if "sysctl" in argv[-1]:
                runner.calls.append((argv, stdin))
                return Result(0, f"{perf_cores}\n".encode(), b"")
            return base(argv, stdin)
        return call, runner

    def test_macos_guest_gets_half_the_host_performance_cores(self):
        for perf_cores, expected in ((8, 4), (4, 2), (2, 2), (12, 6)):
            call, _ = self._runner(perf_cores)
            assert Tart(call, os_name="macos").default_cpus() == expected

    def test_linux_guest_keeps_two_cpus(self):
        call, runner = self._runner(8)
        assert Tart(call, os_name="linux").default_cpus() == 2
        assert not any("sysctl" in c[0][-1] for c in runner.calls)

    def test_run_without_cpus_asks_the_backend_for_its_default(self):
        from sandbox.run import run
        call, runner = self._runner(8)
        backend = Tart(call, os_name="macos")
        run(backend, lambda b, vm: Result(0, b"", b""))
        assert any("--cpu 4 " in c[0][-1] for c in runner.calls)

    def test_explicit_cpus_are_kept(self):
        from sandbox.run import run
        call, runner = self._runner(8)
        run(Tart(call, os_name="macos"), lambda b, vm: Result(0, b"", b""), cpus=3)
        assert any("--cpu 3 " in c[0][-1] for c in runner.calls)
