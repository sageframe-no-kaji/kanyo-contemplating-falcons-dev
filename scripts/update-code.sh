#!/bin/bash
# Quick update script - pull latest code and restart containers

set -e

REMOTE_HOST="${1:-shingan.lan}"
CODE_DIR="/opt/services/kanyo-code"
ADMIN_DIR="/opt/services/kanyo-admin"

if [ "$1" == "" ]; then
    echo "Usage: $0 <remote-host>"
    echo "Example: $0 shingan.lan"
    exit 1
fi

echo "Updating code on ${REMOTE_HOST}..."

# Pull latest code
echo "Pulling latest code..."
ssh "${REMOTE_HOST}" "cd ${CODE_DIR} && git pull"

# Restart containers
echo "Restarting containers..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose restart"

echo ""
echo "✓ Code updated and containers restarted!"
echo ""
echo "To view logs:"
echo "  ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose logs -f'"
