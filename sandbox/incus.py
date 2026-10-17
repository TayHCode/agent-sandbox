import json
import os
import time

from .backend import Backend, check, sh


DEFAULT_IMAGE = "images:ubuntu/24.04/cloud"


def _guest_path(remote):
    return remote.lstrip("/") if remote.startswith("/") else f"root/{remote}"


class Incus(Backend):
    def __init__(self, runner=None):
        self.runner = runner or sh
        self.prefix = ["incus"]
        result = self.runner(["incus", "list", "--format", "json"])
        if result.returncode:
            self.prefix = ["sudo", "-n", "incus"]

    def _run(self, *args, stdin=None):
        return self.runner([*self.prefix, *args], stdin=stdin)

    def create(self, vm, image=None, cpus=2, memory_mb=4096):
        image = image or DEFAULT_IMAGE
        pool = os.environ.get("SANDBOX_INCUS_STORAGE")
        storage = ["--storage", pool] if pool else []
        check(self._run("init", image, vm, "--vm", *storage,
                        "-c", f"limits.cpu={cpus}", "-c", f"limits.memory={memory_mb}MiB"),
              "incus init")
        check(self._run("start", vm), "incus start")
        while True:
            result = self._run("exec", vm, "--", "true")
            if result.returncode == 0:
                return
            time.sleep(1)

    def exec(self, vm, argv, stdin=None):
        return self._run("exec", vm, "--", *argv, stdin=stdin)

    def put(self, vm, local, remote):
        check(self._run("file", "push", "-p", local, f"{vm}/{_guest_path(remote)}"), "incus file push")

    def get(self, vm, remote, local):
        check(self._run("file", "pull", f"{vm}/{_guest_path(remote)}", local), "incus file pull")

    def destroy(self, vm):
        check(self._run("delete", vm, "--force"), "incus delete")

    def list_owned(self):
        result = check(self._run("list", "--format", "json"), "incus list")
        rows = json.loads(result.stdout)
        return [row["name"] for row in rows if row.get("name", "").startswith("sbx-")]
