#!/bin/bash
# Complete production update - pulls latest code, configs, and images

set -e

REMOTE_HOST="${1:-kanyo.lan}"
CODE_DIR="/opt/services/kanyo-code"
ADMIN_DIR="/opt/services/kanyo-admin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ "$1" == "" ]; then
    echo "Usage: $0 <remote-host>"
    echo "Example: $0 kanyo.lan"
    exit 1
fi

echo "Updating production on ${REMOTE_HOST}..."

# Pull latest code
echo "📥 Pulling latest code..."
ssh "${REMOTE_HOST}" "cd ${CODE_DIR} && git pull"

# Sync config files to service directories
echo "📋 Syncing config files..."
for config_dir in harvard nsw; do
    local_config="${PROJECT_DIR}/configs/${config_dir}/config.yaml"
    if [ "$config_dir" = "harvard" ]; then
        service_dir="/opt/services/kanyo-harvard"
    else
        service_dir="/opt/services/kanyo-nsw"
    fi
    if [ -f "$local_config" ]; then
        echo "  → ${service_dir}/config.yaml"
        scp -q "$local_config" "${REMOTE_HOST}:${service_dir}/config.yaml"
    fi
done

# Pull latest docker images from GitHub Container Registry
echo "📦 Pulling latest Docker images..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose pull"

# Rebuild and restart all containers
echo "🔨 Rebuilding and restarting containers..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose up -d --build"

echo ""
echo "✓ Production updated successfully!"
echo ""
echo "To view logs:"
echo "  ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose logs -f'"
echo ""
echo "To check container status:"
echo "  ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose ps'"
