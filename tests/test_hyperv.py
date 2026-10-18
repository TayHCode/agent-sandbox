import base64
import json
import re
import subprocess
from pathlib import Path

from sandbox.hyperv import HyperV
from sandbox.run import run
from tests.support import Case


VM = "sbx-1700000000-aabbcc"


class Commands:

    def __init__(self):
        self.calls = []
        self.requests = []
        self.ips = [None, "guest-vm"]
        self.ready = [255, 0]
        self.fail = None
        self.initcode = 0
        self.exists = False

    def __call__(self, argv, input=None, capture_output=False):
        assert capture_output
        self.calls.append((argv, input))
        code, stdout, stderr = 0, b"", b""
        if argv[0] == "ssh-keygen":
            Path(argv[-1] + ".pub").write_text("ssh-ed25519 AAAA ephemeral")
            Path(argv[-1]).write_text("private")
        elif "powershell" in argv:
            script = input.decode()
            assert argv[-4:] == ["powershell", "-NoProfile", "-Command", "-"]
            assert "Console.InputEncoding" not in script
            encoded = re.search(r"FromBase64String\('([^']+)'\)", script)[1]
            req = json.loads(base64.b64decode(encoded))
            self.requests.append(req)
            action = req["action"]
            if action == self.fail:
                code, stderr = 1, b"Windows failure"
            elif action == "create":
                self.exists = True
            elif action == "destroy":
                self.exists = False
            elif action == "ip":
                stdout = json.dumps(self.ips.pop(0)).encode()
            elif action == "list":
                stdout = json.dumps([VM] if self.exists else []).encode()
        elif argv[0] == "ssh":
            if argv[-1] == "true":
                code = self.ready.pop(0)
            elif argv[-1] == "cloud-init status --wait --long":
                code = self.initcode
                stdout = b"status: error\nprovisioning detail" if code else b"status: done\n"
            else:
                code, stdout, stderr = 7, b"guest output", b"guest error"
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


class TestHyperv(Case):

    def setUp(self):
        super().setUp()
        commands = Commands()
        self.monkeypatch.setattr(subprocess, "run", commands)
        self.monkeypatch.setattr("sandbox.hyperv.shutil.which", lambda tool: "/usr/bin/genisoimage" if tool == "genisoimage" else None)
        self.monkeypatch.setattr("sandbox.hyperv.time.sleep", lambda seconds: None)
        self.commands = commands

    def test_create_waits_for_ip_and_ssh_and_cleans_key(self):
        commands = self.commands
        b = HyperV()
        b.create(VM, None, 3, 5120)
        directory = Path(b.guests[VM]["directory"].name)
        config = json.loads((directory / "user-data").read_text().split("\n", 1)[1])
        assert config["hostname"] == VM
        assert config["packages"] == ["linux-cloud-tools-virtual"]
        assert config["runcmd"] == [["sh", "-c", 'DEBIAN_FRONTEND=noninteractive apt-get install -y "linux-cloud-tools-$(uname -r)"'],
                                   ["udevadm", "control", "--reload-rules"],
                                   ["udevadm", "trigger", "--action=add", "--subsystem-match=misc"],
                                   ["udevadm", "settle"],
                                   ["systemctl", "restart", "hv-kvp-daemon.service"]]
        assert config["users"][0]["ssh_authorized_keys"] == ["ssh-ed25519 AAAA ephemeral"]
        assert config["users"][0]["name"] == "ubuntu"
        assert json.loads((directory / "meta-data").read_text())["instance-id"] == VM
        assert [r["action"] for r in commands.requests] == ["prepare", "create", "ip", "ip"]
        assert commands.requests[1]["cpus"] == "3"
        assert commands.requests[1]["memory_mb"] == "5120"
        assert commands.requests[1]["image"] == r"C:\sandbox\base\ubuntu-24.04.vhdx"
        assert any(a[-1] == f"hyperv-host:C:/sandbox/vms/{VM}.iso" for a, _ in commands.calls)
        assert any(a[-1] == "cloud-init status --wait --long" for a, _ in commands.calls)
        b.destroy(VM)
        assert not directory.exists()
        assert b.list_owned() == []

    def test_exec_and_copies_use_jump_key_and_preserve_exit(self):
        commands = self.commands
        b = HyperV()
        b.create(VM, r"D:\custom.vhdx", 2, 4096)
        result = b.exec(VM, ["printf", "%s", "a; $(touch /tmp/no)"], b"stdin")
        argv, stdin = commands.calls[-1]
        assert result.returncode == 7
        assert result.stdout == b"guest output" and result.stderr == b"guest error"
        assert stdin == b"stdin"
        assert argv[1] == "-o" and argv[2].startswith("ProxyCommand=ssh ")
        assert "ControlMaster=auto" in argv[2] and "-W %h:%p hyperv-host" in argv[2]
        assert argv[-2:] == ["ubuntu@guest-vm", "printf %s 'a; $(touch /tmp/no)'"]
        assert "IdentitiesOnly=yes" in argv
        assert "ForwardAgent=no" in argv
        key = argv[argv.index("-i") + 1]
        b.put(VM, self.tmp_path / "a b", "/tmp/a b")
        argv, _ = commands.calls[-1]
        assert argv[:3] == ["scp", "-r", "-o"] and argv[3].startswith("ProxyCommand=ssh ")
        assert argv[-2:] == [str(self.tmp_path / "a b"), "ubuntu@guest-vm:/tmp/a b"]
        assert argv[argv.index("-i") + 1] == key
        b.get(VM, "/tmp/a b", self.tmp_path / "out")
        assert commands.calls[-1][0][-2:] == ["ubuntu@guest-vm:/tmp/a b", str(self.tmp_path / "out")]
        b.destroy(VM)

    def test_create_failure_run_finally_destroys(self):
        commands = self.commands
        commands.fail = "create"
        b = HyperV()
        with self.assertRaisesRegex(RuntimeError, "Windows failure"):
            run(b, lambda b, vm: b.exec(vm, ["uname", "-a"]))
        assert commands.requests[-2]["action"] == "destroy"
        assert commands.requests[-1]["action"] == "list"
        assert not b.guests

    def test_destroy_failure_still_removes_local_key(self):
        commands = self.commands
        b = HyperV()
        b.create(VM, None, 2, 4096)
        directory = Path(b.guests[VM]["directory"].name)
        commands.fail = "destroy"
        with self.assertRaisesRegex(RuntimeError, "destroy failed"):
            b.destroy(VM)
        assert not directory.exists()

    def test_cloud_init_failure_includes_status_and_cleans_up(self):
        commands = self.commands
        commands.initcode = 1
        b = HyperV()
        with self.assertRaisesRegex(RuntimeError, "provisioning detail"):
            run(b, lambda backend, vm: backend.exec(vm, ["uname", "-a"]))
        assert not b.guests
        assert commands.requests[-2]["action"] == "destroy"

    def _test_seed_builders(self, tool_):
        commands = self.commands
        monkeypatch = self.monkeypatch
        tool = tool_
        self.monkeypatch.setattr("sandbox.hyperv.shutil.which", lambda t: t if t == tool else None)
        assert HyperV()._seed(self.tmp_path, VM, "ssh-ed25519 AAAA") == self.tmp_path / "seed.iso"
        argv, _ = commands.calls[-1]
        assert argv[0] == tool
        if tool == "cloud-localds":
            assert "--disk-format=raw" in argv
        else:
            assert argv[argv.index("-volid") + 1] == "cidata"
            if tool == "xorriso":
                assert argv[1:3] == ["-as", "mkisofs"]

    def test_seed_builders_xorriso(self):
        self._test_seed_builders('xorriso')

    def test_seed_builders_genisoimage(self):
        self._test_seed_builders('genisoimage')

    def test_seed_builders_cloud_localds(self):
        self._test_seed_builders('cloud-localds')

    def test_missing_iso_tool_installs_once(self):
        commands = self.commands
        monkeypatch = self.monkeypatch
        self.monkeypatch.setattr("sandbox.hyperv.shutil.which", lambda t: None)
        b = HyperV()
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "Install xorriso"):
                b._seed(self.tmp_path, VM, "ssh-ed25519 AAAA")
        installs = [argv for argv, _ in commands.calls if argv[0] == "sudo"]
        assert installs == [["sudo", "-n", "apt-get", "install", "-y", "cloud-image-utils"]]

    def test_install_finds_new_tool(self):
        commands = self.commands
        monkeypatch = self.monkeypatch
        self.monkeypatch.setattr("sandbox.hyperv.shutil.which", lambda t:
                            t if t == "cloud-localds" and commands.calls else None)
        HyperV()._seed(self.tmp_path, VM, "ssh-ed25519 AAAA")
        assert commands.calls[0][0][0] == "sudo"
        assert commands.calls[1][0][0] == "cloud-localds"

    def test_list_empty_and_single_are_arrays(self):
        commands = self.commands
        b = HyperV()
        assert b.list_owned() == []
        commands.exists = True
        assert b.list_owned() == [VM]
        b.destroy(VM)
        assert b.list_owned() == []

    def test_run_preserves_guest_failure_and_cleans_up(self):
        commands = self.commands
        b = HyperV()
        result = run(b, lambda backend, vm: backend.exec(vm, ["sh", "-c", "exit 7"]))
        assert result["returncode"] == 7
        assert result["stdout"] == b"guest output"
        assert result["cleaned"]
        assert not b.guests

    def test_power_shell_transmits_untrusted_values_as_data(self):
        commands = self.commands
        b = HyperV()
        image = "C:\\an 'image'; Write-Output 'oops'.vhdx"
        b._ps("create", VM, image=image, cpus="2", memory_mb="4096")
        script = commands.calls[-1][1].decode()
        assert image not in script
        assert commands.requests[-1]["image"] == image
