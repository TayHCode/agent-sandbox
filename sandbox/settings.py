import os
import shlex

WINDOWS_BASE = r"C:\sandbox\base\windows11-eval-oobe-v2.vhdx"


def hyperv_host():
    host = os.environ.get("SANDBOX_HYPERV_HOST")
    if not host:
        raise SystemExit("set SANDBOX_HYPERV_HOST to the SSH target of the Windows Hyper-V host (for example user@host)")
    return host


def tart_prefix():
    host = os.environ.get("SANDBOX_TART_HOST")
    return ["ssh", *shlex.split(host)] if host else ["sh", "-c"]
