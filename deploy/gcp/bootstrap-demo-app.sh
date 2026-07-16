#!/usr/bin/env bash
# One-time host setup for the demo app VM. Run this after SSH-ing in
# (gcloud compute ssh ... --tunnel-through-iap), not as a startup-script.
set -euo pipefail

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  ca-certificates curl git jq unattended-upgrades

# Keep the OS patched automatically — a compromised box that's also months
# behind on kernel/package security fixes is a much worse day.
sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
sudo tee /etc/apt/apt.conf.d/51unattended-upgrades-extra >/dev/null <<'EOF'
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF
sudo systemctl enable --now unattended-upgrades

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "Added $USER to the docker group — log out and back in (or run 'newgrp docker') before using docker without sudo."
fi

mkdir -p ~/unified-fraud-detection
