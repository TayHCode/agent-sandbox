import atexit
import fcntl
import json
import os
import re
import signal
import socket
import sys
import time
from pathlib import Path

from .backend import created_at

NAME = re.compile(r"^sbx-[0-9]+-[0-9a-f]+$")
SLOT_ROOT = Path(os.environ.get("SANDBOX_HOST_SLOTS_DIR", "/var/lib/sandbox/host-slots"))
HELD = {}
LEFTOVER_GRACE_SECONDS = 3600


def lease_dir():
    if os.environ.get("SANDBOX_LEASE_DIR"):
        return Path(os.environ["SANDBOX_LEASE_DIR"])
    shared = Path("/var/lib/sandbox/leases")
    return shared if shared.is_dir() else Path.home() / ".local/state/sandbox/leases"


def publish_host_limit(host, count):
    directory = SLOT_ROOT / host
    directory.mkdir(parents=True, exist_ok=True)
    for number in range(count):
        if not (directory / f"slot-{number}").exists():
            os.close(os.open(directory / f"slot-{number}", os.O_CREAT | os.O_RDONLY, 0o644))
    (directory / "limit").write_text(str(count))


def acquire_host_slot(host):
    directory = SLOT_ROOT / host
    try:
        limit = int((directory / "limit").read_text())
    except (FileNotFoundError, ValueError):
        return None
    while True:
        for number in range(limit):
            fd = os.open(directory / f"slot-{number}", os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                os.close(fd)
        time.sleep(2)


def hold(vm, backend, pool=None):
    if vm in HELD:
        return
    directory = lease_dir()
    directory.mkdir(parents=True, exist_ok=True)
    while True:
        fd = os.open(directory / vm, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.fstat(fd).st_ino == os.stat(directory / vm).st_ino:
                break
        except FileNotFoundError:
            pass
        except BlockingIOError:
            os.close(fd)
            raise
        os.close(fd)
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps(dict(host=socket.gethostname(), pid=os.getpid(), command=" ".join(sys.argv)[:200])).encode())
    HELD[vm] = dict(fd=fd, backend=backend, slot=acquire_host_slot(pool) if pool else None)
    guard()


def release(vm):
    held = HELD.pop(vm, None)
    if held:
        (lease_dir() / vm).unlink(missing_ok=True)
        for fd in (held["fd"], held["slot"]):
            if fd is not None:
                os.close(fd)


def guard(registered=[]):
    if registered:
        return
    registered.append(True)
    atexit.register(destroy_held)
    for number in (signal.SIGTERM, signal.SIGHUP):
        try:
            if signal.getsignal(number) == signal.SIG_DFL:
                signal.signal(number, lambda signum, frame: sys.exit(128 + signum))
        except ValueError:
            pass


def destroy_held():
    for vm, held in list(HELD.items()):
        try:
            held["backend"].destroy(vm)
        except Exception as error:
            print(f"sandbox: could not destroy {vm} at exit: {error}; the reaper will", file=sys.stderr)


def leased(vm):
    try:
        fd = os.open(lease_dir() / vm, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def classify(names):
    """live: a running process holds the lease; orphan: lease file left behind; unowned: no lease file ever."""
    kinds = {}
    for vm in names:
        if not NAME.match(vm):
            continue
        if leased(vm):
            kinds[vm] = "live"
        elif (lease_dir() / vm).exists():
            kinds[vm] = "orphan"
        else:
            kinds[vm] = "unowned"
    return kinds


def forget_dead_leases(listed):
    for path in lease_dir().glob("sbx-*"):
        if path.name in listed:
            continue
        try:
            fd = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path.unlink(missing_ok=True)
        except BlockingIOError:
            pass
        finally:
            os.close(fd)


def reap(backend, dry_run=False, include_unowned=False):
    listed = backend.list_owned()
    kinds = classify(listed)
    doomed = [vm for vm, kind in kinds.items() if kind == "orphan" or (include_unowned and kind == "unowned")]
    list_leftovers = getattr(backend, "list_leftovers", None)
    for vm in (list_leftovers() if list_leftovers else []):
        if NAME.match(vm) and vm not in kinds and not leased(vm) and time.time() - created_at(vm) > LEFTOVER_GRACE_SECONDS:
            kinds[vm] = "leftover"
            doomed.append(vm)
    if not dry_run:
        for vm in doomed:
            backend.destroy(vm)
            (lease_dir() / vm).unlink(missing_ok=True)
    return kinds, doomed


def reap_everywhere(backends, dry_run=False, include_unowned=False):
    report, listed, complete = {}, set(), True
    for label, make in backends:
        try:
            backend = make()
            names = backend.list_owned()
            kinds, doomed = reap(backend, dry_run, include_unowned)
            listed.update(names)
            report[label] = dict(kinds=kinds, reaped=doomed)
        except (Exception, SystemExit) as error:
            complete = False
            report[label] = dict(error=f"{type(error).__name__}: {error}"[:300])
    if complete and not dry_run:
        forget_dead_leases(listed)
    return report
