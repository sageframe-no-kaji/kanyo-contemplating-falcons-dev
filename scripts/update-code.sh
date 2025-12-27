#!/bin/bash
# Quick update script - pull latest code, sync configs, and restart containers

set -e

REMOTE_HOST="${1:-shingan.lan}"
CODE_DIR="/opt/services/kanyo-code"
ADMIN_DIR="/opt/services/kanyo-admin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ "$1" == "" ]; then
    echo "Usage: $0 <remote-host>"
    echo "Example: $0 shingan.lan"
    exit 1
fi

echo "Updating code on ${REMOTE_HOST}..."

# Pull latest code
echo "📥 Pulling latest code..."
ssh "${REMOTE_HOST}" "cd ${CODE_DIR} && git pull"

# Sync config files (not git-tracked, contain site-specific settings)
echo "📋 Syncing config files..."
for config_dir in harvard nsw; do
    local_config="${PROJECT_DIR}/configs/${config_dir}/config.yaml"
    if [ -f "$local_config" ]; then
        echo "  → configs/${config_dir}/config.yaml"
        ssh "${REMOTE_HOST}" "mkdir -p ${CODE_DIR}/configs/${config_dir}"
        scp -q "$local_config" "${REMOTE_HOST}:${CODE_DIR}/configs/${config_dir}/config.yaml"
    fi
done

# Restart containers
echo "🔄 Restarting containers..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose restart"

echo ""
echo "✓ Code updated, configs synced, and containers restarted!"
echo ""
echo "To view logs:"
echo "  ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose logs -f'"
