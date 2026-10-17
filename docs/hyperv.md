# Linux guests on Hyper-V

The controller is a Linux or macOS machine (or WSL) with `ssh`, `scp`, `ssh-keygen` and Python 3.10+. The host is a
Windows machine with Hyper-V, the built-in `Default Switch`, and OpenSSH Server with key login for an account in the
Administrators and Hyper-V Administrators groups.

```sh
export SANDBOX_HYPERV_HOST=user@hyperv-host
ssh -o BatchMode=yes "$SANDBOX_HYPERV_HOST" 'powershell -NoProfile -Command "Get-VMSwitch"'
```

## One-time setup

1. On the controller, download and convert the Ubuntu 24.04 cloud image. The script checks the download against
   Canonical's `SHA256SUMS` and needs `curl` and `qemu-img`:

   ```sh
   scripts/prep-hyperv-image.sh
   ```

   It writes `.scratch/images/ubuntu-24.04.vhdx` with a 24 GB virtual disk; the guest grows its root partition on first boot.

2. Create the folders and copy the image to the host:

   ```sh
   ssh "$SANDBOX_HYPERV_HOST" 'powershell -NoProfile -Command "New-Item -ItemType Directory -Force C:\sandbox\base,C:\sandbox\vms | Out-Null"'
   scp .scratch/images/ubuntu-24.04.vhdx "$SANDBOX_HYPERV_HOST:C:/sandbox/base/ubuntu-24.04.vhdx"
   ssh "$SANDBOX_HYPERV_HOST" 'powershell -NoProfile -Command "Set-ItemProperty C:\sandbox\base\ubuntu-24.04.vhdx -Name IsReadOnly -Value $true"'
   ```

3. Make sure the controller has one of `xorriso`, `genisoimage` or `cloud-localds`. Without one, the backend tries
   `sudo -n apt-get install -y cloud-image-utils` once.

## Smoke test

```sh
python3 -m sandbox run --backend hyperv -- uname -a
ssh "$SANDBOX_HYPERV_HOST" 'powershell -NoProfile -Command "Get-VM sbx-*"'
```

The first command prints Linux and exits 0; the second prints nothing.

## How it works

Each run generates an ed25519 key in a local temporary directory and builds a cloud-init ISO. The VM is a Gen2
differencing disk on the read-only base, attached to the `Default Switch`. Commands and copies use SSH and SCP
through the host as a jump host; the guest user is `ubuntu`. First boot takes a few minutes while cloud-init
installs the Hyper-V guest tools. Destroy removes the VM, its disk, the seed ISO and the local key.
`gc` removes VMs named `sbx-*` whose Notes equal `sandbox-owned`.

`--cpus` and `--memory` (MB) size the VM. Check the host has free RAM before running several at once.
