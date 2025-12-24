#!/bin/bash
# Deploy NVIDIA GPU build to remote machine

set -e

# Configuration
REMOTE_HOST="${1:-gpu-machine.lan}"
DEPLOY_DIR="/opt/services/kanyo-gpu"

if [ "$1" == "" ]; then
    echo "Usage: $0 <remote-host>"
    echo "Example: $0 192.168.1.100"
    echo "Example: $0 gpu-machine.lan"
    exit 1
fi

echo "Deploying NVIDIA GPU build to ${REMOTE_HOST}..."
echo ""

# Step 1: Create directory structure
echo "Creating directory structure..."
ssh -t "${REMOTE_HOST}" "sudo mkdir -p ${DEPLOY_DIR}/data/{harvard,nsw}"

# Step 2: Copy deployment files to temp location
echo "Copying deployment files..."
scp docker-compose.nvidia.yml "${REMOTE_HOST}:/tmp/"
scp .env "${REMOTE_HOST}:/tmp/"
scp data/harvard/config.yaml "${REMOTE_HOST}:/tmp/harvard-config.yaml"
scp data/nsw/config.yaml "${REMOTE_HOST}:/tmp/nsw-config.yaml"

# Step 3: Move files to final location with sudo
echo "Installing files..."
ssh -t "${REMOTE_HOST}" "sudo mv /tmp/docker-compose.nvidia.yml ${DEPLOY_DIR}/docker-compose.yml && \
    sudo mv /tmp/.env ${DEPLOY_DIR}/.env && \
    sudo mv /tmp/harvard-config.yaml ${DEPLOY_DIR}/data/harvard/config.yaml && \
    sudo mv /tmp/nsw-config.yaml ${DEPLOY_DIR}/data/nsw/config.yaml"

echo ""
echo "✓ Deployment files installed!"
echo ""
echo "To start the services:"
echo "  ssh ${REMOTE_HOST} 'cd ${DEPLOY_DIR} && sudo docker compose pull --no-parallel && sudo docker compose up -d'"
echo ""
echo "To view logs:"
echo "  ssh ${REMOTE_HOST} 'cd ${DEPLOY_DIR} && sudo docker compose logs -f'"
echo ""

# Optional: Ask if user wants to start now
read -p "Pull and start containers now? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Starting containers (will pull images if needed)..."
    ssh -t "${REMOTE_HOST}" "cd ${DEPLOY_DIR} && sudo docker compose up -d"
    echo ""
    echo "✓ Containers started!"
    echo ""
    echo "View logs with:"
    echo "  ssh ${REMOTE_HOST} 'cd ${DEPLOY_DIR} && sudo docker compose logs -f'"
fi
