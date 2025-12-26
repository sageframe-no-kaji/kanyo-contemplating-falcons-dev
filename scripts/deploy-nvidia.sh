#!/bin/bash
# Deploy NVIDIA GPU build to remote machine

set -e

# Configuration
REMOTE_HOST="${1:-gpu-machine.lan}"
ADMIN_DIR="/opt/services/kanyo-admin"

HARVARD_DIR="/opt/services/kanyo-harvard"
NSW_DIR="/opt/services/kanyo-nsw"
CODE_DIR="/opt/services/kanyo-code"
GIT_REPO="https://github.com/sageframe-no-kaji/kanyo-contemplating-falcons-dev.git"

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
ssh -t "${REMOTE_HOST}" "sudo mkdir -p ${ADMIN_DIR} && \
    sudo mkdir -p ${HARVARD_DIR}/{data,clips,logs} && \
    sudo mkdir -p ${NSW_DIR}/{data,clips,logs} && \
    sudo mkdir -p ${CODE_DIR} && \
    sudo chown -R 1000:1000 ${HARVARD_DIR} && \
    sudo chown -R 1000:1000 ${NSW_DIR} && \
    sudo chown -R 1000:1000 ${CODE_DIR}"

# Step 1.5: Clone or update code repository
echo "Setting up code repository..."
ssh "${REMOTE_HOST}" "if [ -d ${CODE_DIR}/.git ]; then \
    echo 'Repository exists, pulling latest changes...'; \
    cd ${CODE_DIR} && git pull; \
else \
    echo 'Cloning repository...'; \
    git clone ${GIT_REPO} ${CODE_DIR}; \
fi"

# Step 2: Copy deployment files to temp location
echo "Copying deployment files..."
scp docker/docker-compose.nvidia.yml "${REMOTE_HOST}:/tmp/"
scp .env "${REMOTE_HOST}:/tmp/kanyo.env"
scp configs/harvard/config.yaml "${REMOTE_HOST}:/tmp/harvard-config.yaml"
scp configs/nsw/config.yaml "${REMOTE_HOST}:/tmp/nsw-config.yaml"

# Step 3: Move files to final locations with sudo
echo "Installing files..."
ssh -t "${REMOTE_HOST}" "sudo mv /tmp/docker-compose.nvidia.yml ${ADMIN_DIR}/docker-compose.yml && \
    sudo mv /tmp/kanyo.env ${ADMIN_DIR}/.env && \
    sudo mv /tmp/harvard-config.yaml ${HARVARD_DIR}/config.yaml && \
    sudo mv /tmp/nsw-config.yaml ${NSW_DIR}/config.yaml && \
    sudo chown 1000:1000 ${ADMIN_DIR}/docker-compose.yml && \
    sudo chown 1000:1000 ${ADMIN_DIR}/.env && \
    sudo chown 1000:1000 ${HARVARD_DIR}/config.yaml && \
    sudo chown 1000:1000 ${NSW_DIR}/config.yaml"

echo ""
echo "✓ Deployment complete!"
echo ""
echo "Directory structure:"
echo "  ${ADMIN_DIR}/"
echo "    ├── docker-compose.yml"
echo "    └── .env"
echo ""
echo "  ${HARVARD_DIR}/"
echo "    ├── config.yaml"
echo "    ├── clips/"
echo "    ├── data/"
echo "    └── logs/"
echo ""
echo "  ${NSW_DIR}/"
echo "    ├── config.yaml"
echo "    ├── clips/"
echo "    ├── data/"
echo "    └── logs/"
echo ""
echo "To start the services:"
echo "  ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose pull && sudo docker compose up -d'"
echo ""
echo "To view logs:"
echo "  ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose logs -f'"
echo ""

# Optional: Ask if user wants to start now
read -p "Pull and start containers now? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Pulling images and starting containers..."
    ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose pull && sudo docker compose up -d"
    echo ""
    echo "✓ Containers started!"
    echo ""
    echo "View logs with:"
    echo "  ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose logs -f'"
fi
