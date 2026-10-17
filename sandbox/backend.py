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


def sh(argv, stdin=None):
    p = subprocess.run(argv, input=stdin, capture_output=True)
    return Result(p.returncode, p.stdout, p.stderr)


def check(result, what):
    if result.returncode:
        raise RuntimeError(f"{what} failed ({result.returncode}): {result.stderr.decode(errors='replace').strip()}")
    return result


class Backend:
    """One throwaway VM per name. Names are always sbx-<unix time>-<hex>."""

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
