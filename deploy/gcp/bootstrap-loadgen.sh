#!/usr/bin/env bash
set -euo pipefail
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openjdk-21-jre-headless curl
mkdir -p ~/fraud-loadgen
