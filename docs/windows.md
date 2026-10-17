# Windows guests on Hyper-V

Windows guests use `--backend hyperv --os windows` and a Windows 11 Enterprise Evaluation base disk that you build
once from Microsoft's ISO. This repository does not ship Windows.

## One-time setup

1. On the Hyper-V host, enable [Hyper-V](https://learn.microsoft.com/en-us/virtualization/hyper-v-on-windows/quick-start/enable-hyper-v)
   and [OpenSSH Server](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
   with key login for an Administrators account. On the controller: `export SANDBOX_HYPERV_HOST=user@hyperv-host`.
2. Download the Windows 11 Enterprise Evaluation ISO from the
   [Microsoft Evaluation Center](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise)
   (accept Microsoft's terms; choose the ISO, 64-bit, your language). Save it on the host, for example
   `C:\sandbox\media\Win11_Enterprise_Eval.iso`.
3. Build the base disk. Copy the script to the host and run it in an elevated PowerShell (or over SSH):

   ```sh
   ssh "$SANDBOX_HYPERV_HOST" 'powershell -NoProfile -Command "New-Item -ItemType Directory -Force C:\sandbox\media,C:\sandbox\vms | Out-Null"'
   scp scripts/make-windows-base.ps1 "$SANDBOX_HYPERV_HOST:C:/sandbox/make-windows-base.ps1"
   ssh "$SANDBOX_HYPERV_HOST" 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\sandbox\make-windows-base.ps1 -Iso C:\sandbox\media\Win11_Enterprise_Eval.iso'
   ```

   It applies the ISO's Enterprise image to a new 64 GB dynamic VHDX at `C:\sandbox\base\windows11-eval.vhdx`
   (about 15 GB on disk), makes it bootable, checks the image is generalized, and marks it read-only. Use `-Output`
   for another path and pass the same path with `--image`.

## Use

```sh
python3 -m sandbox run --backend hyperv --os windows -- cmd /c ver
python3 -m sandbox run --backend hyperv --os windows \
  --in ./input.txt:input.txt --out output.txt:./output.txt \
  -- cmd /c 'type input.txt & echo hello>output.txt'
python3 -m sandbox run --backend hyperv --os windows -- cmd /c 'exit 7'
python3 -m sandbox gc --backend hyperv --os windows
```

Commands run in `C:\sandbox` in the guest. Relative `--in`/`--out` paths use that directory; the CLI splits at the
first colon, so use relative guest paths. Output bytes and the exit code are preserved; shell syntax needs an
explicit shell such as `cmd /c` or `powershell -Command`. First boot runs Windows setup and takes a few minutes.

## How it works

Each sandbox is a Gen2 VM with 4096 MiB RAM by default, Secure Boot (MicrosoftWindows template), no network switch
and no vTPM, on a differencing VHDX whose parent is the base disk. A fresh, never-booted child is mounted only to
seed an answer file with a random disposable `sandboxadmin` password, which the controller keeps in memory; Windows
setup necessarily receives it in the answer file, which is removed once PowerShell Direct is ready. Execution uses
PowerShell Direct; file transfers use SCP to host staging plus `Copy-Item -ToSession` / `-FromSession`. Files and
directories are supported.

Creation refuses while any `sbx-*` VM exists, serialized by a host mutex. Cleanup turns off and removes the VM and its
directory under `C:\sandbox\vms`. After `kill -9` of the controller, `sandbox gc --backend hyperv --os windows`
removes VMs named `sbx-*` whose Notes equal `sandbox-owned`. The backend does not change host features, firewall,
switches or services, and never boots the base disk.

A custom `--image` must also be a generalized Windows disk awaiting OOBE. Readiness waits without a deadline;
interrupt the controller if Windows cannot finish setup, then run `gc`.
