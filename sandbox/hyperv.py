import base64
import json
import shlex
import shutil
import tempfile
import time
from pathlib import Path

from .backend import Backend, check, sh
from .settings import hyperv_host


class HyperV(Backend):
    def __init__(self, host=None):
        self.host = host or hyperv_host()
        self.guests = {}
        self.install_attempted = False

    def _ps(self, action, vm=None, **kwargs):
        request = json.dumps(dict(action=action, vm=vm, **kwargs)).encode()
        encoded = base64.b64encode(request).decode()
        script = Path(__file__).with_suffix(".ps1").read_text()
        command = (
            "$request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
            + encoded + "')) | ConvertFrom-Json\n& {\n" + script
            + "\n} -Request $request\n\n"
        )
        return check(sh(["ssh", "-o", "BatchMode=yes", self.host, "powershell",
                         "-NoProfile", "-Command", "-"], command.encode()), action)

    def _seed(self, directory, vm, pubkey):
        tools = ("xorriso", "genisoimage", "cloud-localds")
        tool = next((t for t in tools if shutil.which(t)), None)
        if tool is None and not self.install_attempted:
            self.install_attempted = True
            check(sh(["sudo", "-n", "apt-get", "install", "-y", "cloud-image-utils"]), "ISO tools")
            tool = next((t for t in tools if shutil.which(t)), None)
        if tool is None:
            raise RuntimeError("Install xorriso, genisoimage, or cloud-image-utils")
        user = dict(name="ubuntu", groups=["adm", "sudo"], shell="/bin/bash",
                    sudo="ALL=(ALL) NOPASSWD:ALL", lock_passwd=True,
                    ssh_authorized_keys=[pubkey])
        config = dict(hostname=vm, manage_etc_hosts=True, users=[user],
                      ssh_pwauth=False, disable_root=True, package_update=True,
                      packages=["linux-cloud-tools-virtual"],
                      runcmd=[["sh", "-c", 'DEBIAN_FRONTEND=noninteractive apt-get install -y "linux-cloud-tools-$(uname -r)"'],
                              ["udevadm", "control", "--reload-rules"],
                              ["udevadm", "trigger", "--action=add", "--subsystem-match=misc"],
                              ["udevadm", "settle"],
                              ["systemctl", "restart", "hv-kvp-daemon.service"]])
        (directory / "user-data").write_text("#cloud-config\n" + json.dumps(config))
        (directory / "meta-data").write_text(json.dumps({"instance-id": vm, "local-hostname": vm}))
        iso = directory / "seed.iso"
        files = [str(directory / n) for n in ("user-data", "meta-data")]
        if tool == "cloud-localds":
            argv = [tool, "--disk-format=raw", str(iso), *files]
        else:
            argv = ([tool, "-as", "mkisofs"] if tool == "xorriso" else [tool])
            argv += ["-output", str(iso), "-volid", "cidata", "-joliet", "-rock", *files]
        check(sh(argv), "seed ISO")
        return iso

    def create(self, vm, image, cpus, memory_mb):
        directory = tempfile.TemporaryDirectory(prefix="sandbox-hyperv-")
        self.guests[vm] = dict(directory=directory)
        root = Path(directory.name)
        check(sh(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "key")]), "keygen")
        iso = self._seed(root, vm, (root / "key.pub").read_text().strip())
        self._ps("prepare", vm)
        check(sh(["scp", "-o", "BatchMode=yes", str(iso),
                  f"{self.host}:C:/sandbox/vms/{vm}.iso"]), "upload seed")
        self._ps("create", vm, image=image or r"C:\sandbox\base\ubuntu-24.04.vhdx",
                 cpus=str(cpus), memory_mb=str(memory_mb))
        while True:
            ip = json.loads(self._ps("ip", vm).stdout)
            if ip:
                self.guests[vm]["ip"] = ip
                break
            time.sleep(1)
        while True:
            result = self.exec(vm, ["true"])
            if result.returncode != 255:
                check(result, "guest SSH")
                break
            time.sleep(1)
        result = self.exec(vm, ["cloud-init", "status", "--wait", "--long"])
        if result.returncode:
            result.stderr += result.stdout
        check(result, "cloud-init")

    def _connection(self, vm):
        guest = self.guests[vm]
        root = Path(guest["directory"].name)
        return ["-J", self.host, "-i", str(root / "key"), "-o", "IdentitiesOnly=yes",
                "-o", "ForwardAgent=no",
                "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"UserKnownHostsFile={root / 'known_hosts'}"], f"ubuntu@{guest['ip']}"

    def exec(self, vm, argv, stdin=None):
        options, target = self._connection(vm)
        return sh(["ssh", *options, "--", target, shlex.join(argv)], stdin)

    def put(self, vm, local, remote):
        options, target = self._connection(vm)
        check(sh(["scp", "-r", *options, "--", str(local), f"{target}:{remote}"]), "put")

    def get(self, vm, remote, local):
        options, target = self._connection(vm)
        check(sh(["scp", "-r", *options, "--", f"{target}:{remote}", str(local)]), "get")

    def destroy(self, vm):
        try:
            self._ps("destroy", vm)
        finally:
            guest = self.guests.pop(vm, None)
            if guest:
                guest["directory"].cleanup()

    def list_owned(self):
        return json.loads(self._ps("list").stdout)
