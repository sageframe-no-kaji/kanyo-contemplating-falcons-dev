#!/bin/bash
# Update admin UI only: pull latest code and rebuild admin container

set -e

REMOTE_HOST="${1:-kanyo}"
CODE_DIR="/opt/services/kanyo-code"
ADMIN_DIR="/opt/services/kanyo-admin"

echo "Updating admin on ${REMOTE_HOST}..."

echo "📥 Pulling latest code..."
ssh "${REMOTE_HOST}" "cd ${CODE_DIR} && git pull"

echo "🔨 Rebuilding admin container..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose up -d --build dashboard"

echo ""
echo "✓ Admin updated!"
echo "  Logs: ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose logs -f dashboard'"
