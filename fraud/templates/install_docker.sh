#!/bin/bash
set -e

echo "Installing Docker..."
sudo apt-get update -qq
sudo apt-get install -y -qq docker.io docker-compose-v2 > /dev/null 2>&1
sudo systemctl start docker
sudo systemctl enable docker

echo "✓ Docker installed successfully"
docker --version
docker compose version
