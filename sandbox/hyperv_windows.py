import base64
import json
import secrets
import subprocess
import time
import uuid
from pathlib import Path

from .backend import Backend, Result, check, sh
from .settings import WINDOWS_BASE, hyperv_host


class HyperVWindows(Backend):
    def __init__(self, host=None):
        self.host = host or hyperv_host()
        self.guests = {}

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

    def create(self, vm, image, cpus, memory_mb):
        self.guests[vm] = dict(password="Aa9!" + secrets.token_hex(32))
        self._ps("create", vm, image=image or WINDOWS_BASE,
                 cpus=str(cpus), memory_mb=str(memory_mb), **self.guests[vm])
        while not json.loads(self._ps("ready", vm, **self.guests[vm]).stdout):
            time.sleep(1)

    def exec(self, vm, argv, stdin=None):
        response = json.loads(self._ps(
            "exec", vm, **self.guests[vm], executable=argv[0],
            arguments=subprocess.list2cmdline(argv[1:]),
            stdin=base64.b64encode(stdin or b"").decode()).stdout)
        return Result(int(response["returncode"]), base64.b64decode(response["stdout"]),
                      base64.b64decode(response["stderr"]))

    def put(self, vm, local, remote):
        stage = uuid.uuid4().hex
        self._ps("stage", vm, stage=stage)
        path = f"C:/sandbox/vms/{vm}/{stage}/payload"
        try:
            check(sh(["scp", "-r", "-o", "BatchMode=yes", "--", str(local),
                      f"{self.host}:{path}"]), "upload")
            self._ps("put", vm, **self.guests[vm], stage=stage, remote=remote)
        finally:
            self._ps("unstage", vm, stage=stage)

    def get(self, vm, remote, local):
        stage = uuid.uuid4().hex
        self._ps("stage", vm, stage=stage)
        path = f"C:/sandbox/vms/{vm}/{stage}/payload"
        try:
            self._ps("get", vm, **self.guests[vm], stage=stage, remote=remote)
            check(sh(["scp", "-r", "-o", "BatchMode=yes", "--",
                      f"{self.host}:{path}", str(local)]), "download")
        finally:
            self._ps("unstage", vm, stage=stage)

    def destroy(self, vm):
        try:
            self._ps("destroy", vm)
        finally:
            self.guests.pop(vm, None)

    def list_owned(self):
        return json.loads(self._ps("list").stdout)
