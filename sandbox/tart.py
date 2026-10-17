import json
import os
import shlex
import subprocess
import time

from .backend import Backend, Result, check
from .settings import tart_prefix


DEFAULT_IMAGE = "ghcr.io/cirruslabs/ubuntu:latest"
DEFAULT_MACOS_IMAGE = "ghcr.io/cirruslabs/macos-sequoia-base:latest"
HOME_SHELL = ["sh", "-c", 'cd "$HOME" && exec "$@"', "sh"]
REMOTE_PATH = "$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"


class Tart(Backend):
    def __init__(self, runner=None, os_name="linux"):
        self.runner = runner or self._run
        self.os_name = os_name
        self.remote = tart_prefix()

    @staticmethod
    def _run(argv, stdin=None):
        p = subprocess.run(argv, input=stdin, capture_output=True)
        return Result(p.returncode, p.stdout, p.stderr)

    def _tart(self, *args, stdin=None):
        return self.runner([*self.remote, f"PATH={REMOTE_PATH} tart " + " ".join(shlex.quote(a) for a in args)], stdin=stdin)

    def create(self, vm, image=None, cpus=2, memory_mb=4096):
        image = image or (DEFAULT_MACOS_IMAGE if self.os_name == "macos" else DEFAULT_IMAGE)
        check(self._tart("clone", image, vm), "tart clone")
        check(self._tart("set", vm, "--cpu", str(cpus), "--memory", str(memory_mb)), "tart set")
        start = self.runner([*self.remote, f"PATH={REMOTE_PATH} nohup tart run {shlex.quote(vm)} --no-graphics >/dev/null 2>&1 </dev/null &" ])
        check(start, "tart run")
        while True:
            result = self._tart("exec", vm, "true")
            if result.returncode == 0:
                return
            time.sleep(1)

    def exec(self, vm, argv, stdin=None):
        flags = ["-i"] if stdin is not None else []
        shell = ["/bin/sh", *HOME_SHELL[1:]] if self.os_name == "macos" else HOME_SHELL
        return self._tart("exec", *flags, vm, *shell, *argv, stdin=stdin)

    def put(self, vm, local, remote):
        parent = remote.rpartition("/")[0] or "."
        check(self.exec(vm, ["mkdir", "-p", parent]), "tart exec mkdir")
        packed = subprocess.run(["tar", "-C", os.path.dirname(local) or ".", "-cf", "-", os.path.basename(local)], capture_output=True)
        check(Result(packed.returncode, packed.stdout, packed.stderr), "tar create")
        check(self.exec(vm, ["tar", "-xf", "-", "-C", parent], stdin=packed.stdout), "tart exec tar")

    def get(self, vm, remote, local):
        parent = remote.rpartition("/")[0] or "."
        name = remote.rpartition("/")[2]
        packed = check(self.exec(vm, ["tar", "-C", parent, "-cf", "-", name]), "tart exec tar")
        target = local.rpartition("/")[0] or "."
        extracted = subprocess.run(["tar", "-xf", "-", "-C", target], input=packed.stdout, capture_output=True)
        check(Result(extracted.returncode, extracted.stdout, extracted.stderr), "tar extract")

    def destroy(self, vm):
        self._tart("stop", vm)
        check(self._tart("delete", vm), "tart delete")

    def list_owned(self):
        result = check(self._tart("list", "--format", "json"), "tart list")
        rows = json.loads(result.stdout)
        return [row["Name"] for row in rows if row.get("Name", "").startswith("sbx-")]
