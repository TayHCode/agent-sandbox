#!/usr/bin/env bash
set -euo pipefail

if (($# != 3)); then
  echo "Usage: $0 <vm-name> <pubkey-file> <out.iso>" >&2
  exit 2
fi
vm=$1
key=$2
out=$3
[[ $vm =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || { echo "Invalid VM name" >&2; exit 2; }
[[ -r $key ]] || { echo "Cannot read public key: $key" >&2; exit 2; }
command -v cloud-localds >/dev/null
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
cat > "$scratch/user-data" <<EOF_CLOUD
#cloud-config
hostname: $vm
manage_etc_hosts: true
users:
  - default
  - name: ubuntu
    gecos: Ubuntu User
    groups: [adm, sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: true
    ssh_authorized_keys:
      - $(cat "$key")
ssh_pwauth: false
disable_root: true
package_update: true
packages:
  - linux-cloud-tools-virtual
EOF_CLOUD
cat > "$scratch/meta-data" <<EOF_META
instance-id: $vm
local-hostname: $vm
EOF_META
mkdir -p "$(dirname "$out")"
cloud-localds "$out" "$scratch/user-data" "$scratch/meta-data"
