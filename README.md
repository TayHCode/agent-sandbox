# sandbox

Boot a throwaway VM, run a command in it, copy files in and out, and delete the VM. One CLI for three
hypervisors and three guest operating systems, so an agent can test things on a clean machine of the right kind.

    python3 -m sandbox run --backend incus|tart|hyperv --os linux|macos|windows [options] -- <command...>

The command's exit code is `sandbox`'s exit code. Every VM is named `sbx-<unix time>-<hex>`, and that name is the
only ownership record, so `gc` is just a list and a delete.

| Backend | Host it drives | `--os linux` | `--os macos` | `--os windows` |
|---------|----------------|--------------|--------------|----------------|
| `incus` | Linux with [Incus](https://linuxcontainers.org/incus/) | yes | no | no |
| `tart` | Apple silicon Mac with [Tart](https://tart.run/) | yes | yes | no |
| `hyperv` | Windows with [Hyper-V](https://learn.microsoft.com/en-us/virtualization/hyper-v-on-windows/about/), reached over SSH | yes | no | yes |

## Quick start for agents

You need Python 3.10 or newer and Git. The sandbox has no Python dependencies and needs no install: clone
https://github.com/TayHCode/agent-sandbox, enter the directory, and run these from its root.

```sh
python3 -m unittest discover -s tests -t . -q
python3 -m sandbox --help
```

The tests use fakes and need no hypervisor. Running real VMs needs a hypervisor host, covered next.

## Host setup by platform

Pick the section for the machine that will host the VMs and install its dependencies from
[Dependencies](#dependencies). A working setup prints the guest's version string and exits 0, and `sandbox gc`
then prints nothing.

### Linux host, Linux guest (Incus)

```sh
python3 -m sandbox run --backend incus -- uname -a
python3 -m sandbox gc --backend incus
```

Incus must work for your user (`incus list` succeeds, or `sudo -n incus list` does). If your default profile has no
root disk, point the sandbox at a storage pool: `export SANDBOX_INCUS_STORAGE=<pool name>`.

### Apple silicon Mac, Linux or macOS guest (Tart)

```sh
python3 -m sandbox run --backend tart -- uname -a
python3 -m sandbox run --backend tart --os macos -- sw_vers
python3 -m sandbox gc --backend tart
```

Run on the Mac itself, or from any machine by setting `export SANDBOX_TART_HOST=user@mac-host` (SSH key login is
required, and Tart must be on the Mac's `PATH`, `~/.local/bin` or `/opt/homebrew/bin`). The first run pulls the
guest image, which is large for macOS (about 25 GB download).

### Windows host, Linux or Windows guest (Hyper-V)

The controller (where you run `python3 -m sandbox`) is a Linux or macOS machine, or WSL, with `ssh` and `scp`. The
Hyper-V host is a Windows machine with OpenSSH Server and key login. One-time setup is in
[docs/hyperv.md](docs/hyperv.md) for Linux guests and [docs/windows.md](docs/windows.md) for Windows guests.

```sh
export SANDBOX_HYPERV_HOST=user@hyperv-host
python3 -m sandbox run --backend hyperv -- uname -a
python3 -m sandbox run --backend hyperv --os windows -- cmd /c ver
python3 -m sandbox gc --backend hyperv --os windows
```

## Usage

```
sandbox run   --backend B [--os linux|macos|windows] [--image X] [--cpus N] [--memory MB]
              [--in LOCAL[:REMOTE]] [--out REMOTE[:LOCAL]] [--result-dir DIR] -- <command...>
sandbox agent --backend B [--os OS] --task "..." --model-cmd "<prompt on stdin, next command on stdout>"
sandbox gc    --backend B [--os OS] [--older-than MINUTES]
```

`--os` defaults to `linux`. Unsupported combinations fail immediately (for example `--backend incus --os macos`).
`--image` overrides the backend's default image for the chosen OS.

Copy a file in, run a command, copy the result out:

```sh
echo hello > input.txt
python3 -m sandbox run --backend incus --in input.txt:input.txt --out output.txt:./output.txt \
  -- sh -c 'tr a-z A-Z < input.txt > output.txt'
cat output.txt
```

Use relative guest paths with `--in` and `--out`. They resolve in the guest user's home directory on Linux and macOS
and in `C:\sandbox` on Windows. `--result-dir DIR` also writes `result.json`, `stdout` and `stderr`.

Exit codes: the command's own code, or `70` if the VM could not be deleted. After a killed run, `sandbox gc` deletes
every leftover `sbx-*` VM on that backend.

## Configuration

| Variable | Used by | Meaning |
|----------|---------|---------|
| `SANDBOX_HYPERV_HOST` | `hyperv` (required) | SSH target of the Windows Hyper-V host, for example `user@hyperv-host` |
| `SANDBOX_TART_HOST` | `tart` (optional) | SSH target of the Mac. Unset runs Tart on the local machine |
| `SANDBOX_INCUS_STORAGE` | `incus` (optional) | Storage pool for sandbox VMs. Unset uses the default pool |

Default images: `images:ubuntu/24.04/cloud` (Incus), `ghcr.io/cirruslabs/ubuntu:latest` and
`ghcr.io/cirruslabs/macos-sequoia-base:latest` (Tart), `C:\sandbox\base\ubuntu-24.04.vhdx` and
`C:\sandbox\base\windows11-eval.vhdx` on the Hyper-V host.

## Dependencies

Download only from the official sources below. Install only what your platform needs.

| Needed for | Dependency | Official source |
|------------|------------|-----------------|
| Everything | Python 3.10+ | [python.org downloads](https://www.python.org/downloads/) |
| Everything | Git | [git-scm.com downloads](https://git-scm.com/downloads) |
| `incus` | Incus (with KVM for VMs) | [Incus installation](https://linuxcontainers.org/incus/docs/main/installing/) |
| `incus` | Ubuntu guest image | Downloaded by Incus from its public image server, see [Incus images](https://linuxcontainers.org/incus/docs/main/howto/images_remote/) |
| `tart` | Tart (Apple silicon Mac, macOS 13 or newer) | [Tart quick start](https://tart.run/quick-start/), [releases](https://github.com/cirruslabs/tart/releases) |
| `tart` macOS guest | Cirrus macOS images | [Tart quick start](https://tart.run/quick-start/), [macos-image-templates](https://github.com/cirruslabs/macos-image-templates) |
| `tart` Linux guest | Cirrus Ubuntu image | [linux-image-templates](https://github.com/cirruslabs/linux-image-templates) |
| `hyperv` | Hyper-V (Windows Pro, Enterprise or Education) | [Enable Hyper-V](https://learn.microsoft.com/en-us/virtualization/hyper-v-on-windows/quick-start/enable-hyper-v) |
| `hyperv` | OpenSSH Server on the Windows host | [Get started with OpenSSH for Windows](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse) |
| `hyperv` Linux guest | Ubuntu 24.04 cloud image | [cloud-images.ubuntu.com, noble](https://cloud-images.ubuntu.com/releases/noble/release/) |
| `hyperv` Linux guest | `qemu-img` (converts the image to VHDX) | [QEMU downloads](https://www.qemu.org/download/) |
| `hyperv` Linux guest | `xorriso`, `genisoimage` or `cloud-localds` (seed ISO) | Your distribution's `xorriso` or `cloud-image-utils` package, see [cloud-init NoCloud](https://cloudinit.readthedocs.io/en/latest/reference/datasources/nocloud.html) |
| `hyperv` Windows guest | Windows 11 Enterprise Evaluation ISO | [Microsoft Evaluation Center](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise) |

The Windows 11 Enterprise Evaluation is a time-limited evaluation from Microsoft. Accept Microsoft's terms on the
download page; this repository does not distribute Windows or macOS images. macOS guests run on Apple hardware only,
under Apple's license terms.

## Limitations

- There are no timeouts, resource caps or quotas. Use `--cpus` and `--memory`, and run `sandbox gc` after any killed run.
- Hyper-V guests: only one sandbox at a time for Windows guests (creation refuses while another `sbx-*` VM exists),
  and Windows guests have no network switch, so they cannot reach the internet.
- macOS guests: Apple allows at most two macOS VMs at once on one Mac. Only images with the Tart guest agent work,
  such as the Cirrus `macos-*-base` images.
- The Windows 11 evaluation disk expires on Microsoft's schedule; build a fresh base from a current ISO.
- The `agent` command executes whatever commands your model command proposes inside the VM. Treat the VM, not the
  host, as the boundary, and do not give guests credentials.
- The Hyper-V backend is driven from a Linux or macOS controller. A Windows controller is untested.
- This is a small tool, not a security product: the guest is a normal VM with the isolation your hypervisor gives it.

## Development

```sh
python3 -m unittest discover -s tests -t . -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
