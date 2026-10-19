import itertools
import json
import os
import pwd
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .backend import Backend, Result, check, give_to_invoker, invoker, sh
from .settings import tart_prefix


DEFAULT_IMAGE = "ghcr.io/cirruslabs/ubuntu:latest"
DEFAULT_MACOS_IMAGE = "ghcr.io/cirruslabs/macos-sequoia-base:latest"
MIN_MEMORY_MB = 8192
HOME_SHELL = ["sh", "-c", 'cd "$HOME" && exec "$@"', "sh"]
REMOTE_PATH = "$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
PROBE_SECONDS = 2


def shared_ssh(prefix):
    if prefix[0] != "ssh":
        return prefix
    user = invoker()
    home = Path(pwd.getpwnam(user).pw_dir) if user else Path.home()
    mux = home / ".ssh" / "sandbox-mux"
    mux.mkdir(mode=0o700, parents=True, exist_ok=True)
    give_to_invoker(mux)
    return ["ssh", "-o", "ControlMaster=auto", "-o", f"ControlPath={mux}/%C", "-o", "ControlPersist=120", *prefix[1:]]


class Tart(Backend):
    def __init__(self, runner=None, os_name="linux"):
        self.runner = runner or self._run
        self.os_name = os_name
        self.remote = shared_ssh(tart_prefix())
        host = os.environ.get("SANDBOX_TART_HOST")
        self.pool = host.split()[-1].rpartition("@")[2] if host else None

    @staticmethod
    def _run(argv, stdin=None):
        return sh(argv, stdin)

    def _tart(self, *args, stdin=None):
        return self.runner([*self.remote, f"PATH={REMOTE_PATH} tart " + " ".join(shlex.quote(a) for a in args)], stdin=stdin)

    def _probe(self, vm):
        command = f"perl -e 'alarm {PROBE_SECONDS}; exec @ARGV' tart exec {shlex.quote(vm)} true"
        return self.runner([*self.remote, f"PATH={REMOTE_PATH} {command}"])

    def default_cpus(self):
        if self.os_name != "macos":
            return 2
        result = self.runner([*self.remote, "sysctl -n hw.perflevel0.logicalcpu || sysctl -n hw.ncpu"])
        cores = int(result.stdout.split()[0]) if result.returncode == 0 and result.stdout.split() else 4
        return max(2, cores // 2)

    def create(self, vm, image=None, cpus=2, memory_mb=4096):
        image = image or (DEFAULT_MACOS_IMAGE if self.os_name == "macos" else DEFAULT_IMAGE)
        check(self._tart("clone", image, vm), "tart clone")
        check(self._tart("set", vm, "--cpu", str(cpus), "--memory", str(max(memory_mb, MIN_MEMORY_MB))), "tart set")
        self._start(vm)
        restarted = False
        for attempt in itertools.count():
            result = self._probe(vm)
            if result.returncode == 0:
                return
            if attempt >= 10 and self._state(vm) == "stopped":
                if restarted:
                    raise RuntimeError(f"tart guest {vm} stopped before it answered twice")
                restarted = True
                self._start(vm)
            time.sleep(0.5)

    def _start(self, vm):
        check(self.runner([*self.remote, f"PATH={REMOTE_PATH} nohup tart run {shlex.quote(vm)} --no-graphics >/dev/null 2>&1 </dev/null &"]), "tart run")

    def _state(self, vm):
        rows = json.loads(check(self._tart("list", "--format", "json"), "tart list").stdout)
        return next((row.get("State", "").lower() for row in rows if row.get("Name") == vm), "")

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
        with tempfile.TemporaryDirectory() as staging:
            extracted = subprocess.run(["tar", "-xf", "-", "-C", staging], input=packed.stdout, capture_output=True)
            check(Result(extracted.returncode, extracted.stdout, extracted.stderr), "tar extract")
            shutil.move(os.path.join(staging, name), local)

    def destroy(self, vm):
        self._tart("stop", vm, "--timeout", "0")
        deleted = self._tart("delete", vm)
        if deleted.returncode and b"does not exist" not in deleted.stderr:
            check(deleted, "tart delete")

    def list_owned(self):
        result = check(self._tart("list", "--format", "json"), "tart list")
        rows = json.loads(result.stdout)
        return [row["Name"] for row in rows if row.get("Name", "").startswith("sbx-")]
