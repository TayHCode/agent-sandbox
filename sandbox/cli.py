import argparse
import sys
from pathlib import Path

from .agent import drive
from .run import gc, run


def load_backend(name, os_name="linux"):
    if name == "incus" and os_name == "linux":
        from .incus import Incus
        return Incus()
    if name == "tart" and os_name in ("linux", "macos"):
        from .tart import Tart
        return Tart(os_name=os_name)
    if name == "hyperv" and os_name == "linux":
        from .hyperv import HyperV
        return HyperV()
    if name == "hyperv" and os_name == "windows":
        from .hyperv_windows import HyperVWindows
        return HyperVWindows()
    raise SystemExit(f"backend {name} has no {os_name} guests")


def pair(text):
    a, _, b = text.partition(":")
    return a, b


def main(argv=None):
    p = argparse.ArgumentParser(prog="sandbox")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "agent", "gc"):
        s = sub.add_parser(name)
        s.add_argument("--backend", required=True, choices=["incus", "tart", "hyperv"])
        s.add_argument("--os", dest="os_name", choices=["linux", "windows", "macos"], default="linux")
        if name == "gc":
            s.add_argument("--dry-run", action="store_true")
            s.add_argument("--include-unowned", action="store_true")
            continue
        s.add_argument("--image")
        s.add_argument("--cpus", type=int)
        s.add_argument("--memory", type=int, default=4096, metavar="MB")
        s.add_argument("--in", dest="inputs", action="append", default=[], metavar="LOCAL[:REMOTE]")
        s.add_argument("--out", dest="outputs", action="append", default=[], metavar="REMOTE[:LOCAL]")
        s.add_argument("--result-dir")
        if name == "run":
            s.add_argument("command", nargs=argparse.REMAINDER)
        else:
            s.add_argument("--task", required=True)
            s.add_argument("--model-cmd", required=True, help="shell command: prompt on stdin, next action on stdout")
    args = p.parse_args(argv)
    backend = load_backend(args.backend, args.os_name)
    if args.cmd == "gc":
        for vm in gc(backend, args.dry_run, args.include_unowned):
            print(vm)
        return 0
    command = args.command[1:] if args.cmd == "run" and args.command[:1] == ["--"] else getattr(args, "command", None)
    if args.cmd == "run":
        if not command:
            p.error("missing command after --")
        work = lambda b, vm: b.exec(vm, command)
    else:
        work = lambda b, vm: drive(b, vm, args.task, args.model_cmd)
    inputs = [(l, r or Path(l).name) for l, r in map(pair, args.inputs)]
    outputs = []
    for r, l in map(pair, args.outputs):
        outputs.append((r, l or str(Path(args.result_dir or ".") / Path(r).name)))
    result = run(backend, work, args.image, args.cpus, args.memory, inputs, outputs, args.result_dir)
    sys.stdout.buffer.write(result.get("stdout", b""))
    sys.stderr.buffer.write(result.get("stderr", b""))
    if not result.get("cleaned"):
        print(f"sandbox: {result['vm']} was not cleaned up; run `sandbox gc --backend {args.backend}`", file=sys.stderr)
        return 70
    return result["returncode"]


if __name__ == "__main__":
    raise SystemExit(main())
