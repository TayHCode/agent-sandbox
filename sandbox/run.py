import json
import os
import time
from pathlib import Path

from .backend import created_at, new_name


def run(backend, work, image=None, cpus=2, memory_mb=4096, inputs=(), outputs=(), result_dir=None):
    vm = new_name()
    started = time.time()
    result = {"backend": type(backend).__name__, "vm": vm}
    out = Path(result_dir) if result_dir else None
    try:
        backend.create(vm, image, cpus, memory_mb)
        for local, remote in inputs:
            backend.put(vm, local, remote)
        r = work(backend, vm)
        result.update(returncode=r.returncode, stdout=r.stdout, stderr=r.stderr)
        for remote, local in outputs:
            Path(local).parent.mkdir(parents=True, exist_ok=True)
            backend.get(vm, remote, local)
    except BaseException as e:
        result.setdefault("returncode", 255)
        result["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        try:
            backend.destroy(vm)
            result["cleaned"] = vm not in backend.list_owned()
        except Exception as e:
            result["cleaned"] = False
            result["cleanup_error"] = f"{type(e).__name__}: {e}"
        result["seconds"] = round(time.time() - started, 1)
        if out:
            out.mkdir(parents=True, exist_ok=True)
            (out / "stdout").write_bytes(result.get("stdout", b""))
            (out / "stderr").write_bytes(result.get("stderr", b""))
            meta = {k: v for k, v in result.items() if k not in ("stdout", "stderr")}
            (out / "result.json").write_text(json.dumps(meta, indent=2) + "\n")
    return result


def gc(backend, older_than_minutes=0):
    cutoff = time.time() - older_than_minutes * 60
    removed = []
    for vm in backend.list_owned():
        if created_at(vm) <= cutoff:
            backend.destroy(vm)
            removed.append(vm)
    return removed
