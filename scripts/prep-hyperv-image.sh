#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
images="$root/.scratch/images"
base=https://cloud-images.ubuntu.com/releases/noble/release
name=ubuntu-24.04-server-cloudimg-amd64.img
mkdir -p "$images"
cd "$images"
curl -fL "$base/$name" -o "$name"
curl -fL "$base/SHA256SUMS" -o SHA256SUMS
grep -F "*$name" SHA256SUMS | sed 's/\*/ /' | sha256sum --check --status
/usr/bin/qemu-img convert -p -O vhdx -o subformat=dynamic "$name" ubuntu-24.04.vhdx
/usr/bin/qemu-img resize ubuntu-24.04.vhdx 32G
printf '%s\n' 'C:/sandbox/base/ubuntu-24.04.vhdx'
