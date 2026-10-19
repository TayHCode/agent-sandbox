import os
import pwd
import subprocess
import time
import uuid
from dataclasses import dataclass

PREFIX = "sbx-"


@dataclass
class Result:
    returncode: int
    stdout: bytes
    stderr: bytes


def new_name():
    return f"{PREFIX}{int(time.time())}-{uuid.uuid4().hex[:6]}"


def created_at(name):
    return int(name[len(PREFIX):].split("-")[0])


def invoker():
    user = os.environ.get("SUDO_USER")
    return user if os.geteuid() == 0 and user and user != "root" else None


def give_to_invoker(path):
    user = invoker()
    if user:
        entry = pwd.getpwnam(user)
        os.chown(path, entry.pw_uid, entry.pw_gid)
    return path


def sh(argv, stdin=None):
    user = invoker()
    if user and argv[0] in ("ssh", "scp", "ssh-keygen"):
        argv = ["runuser", "-u", user, "--", "env", "HOME=" + pwd.getpwnam(user).pw_dir, *argv]
    p = subprocess.run(argv, input=stdin, capture_output=True)
    return Result(p.returncode, p.stdout, p.stderr)


def check(result, what):
    if result.returncode:
        raise RuntimeError(f"{what} failed ({result.returncode}): {result.stderr.decode(errors='replace').strip()}")
    return result


class Backend:
    """One throwaway VM per name. Names are always sbx-<unix time>-<hex>."""

    windows = False
    pool = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        raw_create, raw_destroy = cls.__dict__.get("create"), cls.__dict__.get("destroy")
        if raw_create:
            def create(self, vm, *args, **kw):
                from . import lease
                lease.hold(vm, self, self.pool)
                return raw_create(self, vm, *args, **kw)
            cls.create = create
        if raw_destroy:
            def destroy(self, vm):
                from . import lease
                raw_destroy(self, vm)
                lease.release(vm)
            cls.destroy = destroy

    def default_cpus(self):
        return 2

    def create(self, vm, image, cpus, memory_mb):
        raise NotImplementedError

    def exec(self, vm, argv, stdin=None):
        raise NotImplementedError

    def put(self, vm, local, remote):
        raise NotImplementedError

    def get(self, vm, remote, local):
        raise NotImplementedError

    def destroy(self, vm):
        raise NotImplementedError

    def list_owned(self):
        """Names of every sbx-* VM this backend can see."""
        raise NotImplementedError
