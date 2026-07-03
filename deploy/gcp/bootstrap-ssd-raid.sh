#!/usr/bin/env bash
# RAID0 all NVMe local SSDs onto /data for high-throughput CSV generation.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

apt-get update -qq
apt-get install -y -qq mdadm python3

# Local SSDs are 375G; boot disk is smaller (e.g. 200G nvme0n1).
mapfile -t DISKS < <(lsblk -dpno NAME,SIZE,TYPE | awk '$3=="disk" && $2 ~ /^375G/ {print $1}')
if [[ ${#DISKS[@]} -lt 1 ]]; then
  mapfile -t DISKS < <(lsblk -dpno NAME,SIZE,TYPE | awk '$3=="disk" && $1 != "/dev/nvme0n1" {print $1}')
fi
if [[ ${#DISKS[@]} -lt 1 ]]; then
  echo "No NVMe disks found"
  lsblk
  exit 1
fi

echo "Found disks: ${DISKS[*]}"

if ! mdadm --detail /dev/md0 &>/dev/null; then
  echo yes | mdadm --create /dev/md0 --level=0 --raid-devices=${#DISKS[@]} "${DISKS[@]}"
  mkfs.ext4 -F /dev/md0
fi

mkdir -p /data
if ! grep -q '/data' /etc/fstab; then
  echo '/dev/md0 /data ext4 defaults,nofail 0 2' >> /etc/fstab
fi
mount -a

chown -R "${SUDO_USER:-lyndon}:$(id -gn "${SUDO_USER:-lyndon}")" /data
df -h /data
echo "RAID0 ready at /data"
