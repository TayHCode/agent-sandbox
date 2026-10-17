# Backends

Each VM is named `sbx-<unix time>-<hex>`. A backend implements `create / exec / put / get / destroy / list_owned`
(see `sandbox/backend.py`).

## incus

Uses `incus` directly, or `sudo -n incus` when your user cannot talk to the daemon. Creates `--vm` guests from
`images:ubuntu/24.04/cloud` and waits for the in-guest agent. `SANDBOX_INCUS_STORAGE` selects a storage pool.

## tart

Runs `tart` locally, or over SSH when `SANDBOX_TART_HOST` is set. The default Linux image is
`ghcr.io/cirruslabs/ubuntu:latest`. `--os macos` uses `ghcr.io/cirruslabs/macos-sequoia-base:latest`; `--image`
overrides either. Images must include the Tart guest agent, which `tart exec` needs; the Cirrus base images do.
Commands run as the image's `admin` user in its home directory, and file transfer uses tar over the agent.

```sh
python3 -m sandbox run --backend tart --os macos -- sw_vers
python3 -m sandbox run --backend tart --os macos --in input.txt --out output.txt -- sh -c 'cat input.txt; cp input.txt output.txt'
```

Run one macOS sandbox at a time; Apple permits two concurrent macOS VMs. The macOS base image is a download of about
25 GB and occupies about 33 GB once cached. APFS clones share its blocks and grow as the guest writes. Check free
disk (`df -h /`) before the first pull. `sandbox gc --backend tart` stops and deletes leftover `sbx-*` guests.

## hyperv

Needs a Windows host with Hyper-V and key-based OpenSSH, named by `SANDBOX_HYPERV_HOST`. Linux guests: see
[hyperv.md](hyperv.md). Windows guests: see [windows.md](windows.md).

## Cleanup

`sandbox gc --backend B [--os OS] [--older-than MINUTES]` deletes every `sbx-*` VM on that backend. Exit code 70 from
`run` means the cleanup in its `finally` block failed.

## Guest OS (`--os`)

| Backend | linux | macos | windows |
|---------|-------|-------|---------|
| incus | yes | no | no |
| tart | yes | yes | no |
| hyperv | yes | no | yes |
