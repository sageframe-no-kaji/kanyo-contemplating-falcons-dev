#!/bin/bash
# Quick admin GUI update script - pull latest code and rebuild admin container only

set -e

REMOTE_HOST="${1:-shingan.lan}"
CODE_DIR="/opt/services/kanyo-code"
ADMIN_DIR="/opt/services/kanyo-admin"

if [ "$1" == "" ]; then
    echo "Usage: $0 <remote-host>"
    echo "Example: $0 shingan.lan"
    exit 1
fi

echo "Updating admin GUI on ${REMOTE_HOST}..."

# Pull latest code
echo "📥 Pulling latest code..."
ssh "${REMOTE_HOST}" "cd ${CODE_DIR} && git pull"

# Rebuild admin container with new code
echo "🔨 Rebuilding admin container..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose up -d --build dashboard"

echo ""
echo "✓ Admin GUI updated!"
echo ""
echo "To view logs:"
echo "  ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose logs -f dashboard'"
