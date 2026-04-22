#!/bin/bash
# Update detection code: pull latest, rebuild admin, restart all stream containers

set -e

REMOTE_HOST="${1:-kanyo}"
CODE_DIR="/opt/services/kanyo-code"
ADMIN_DIR="/opt/services/kanyo-admin"

echo "Updating code on ${REMOTE_HOST}..."

echo "📥 Pulling latest code..."
ssh "${REMOTE_HOST}" "cd ${CODE_DIR} && git pull"

echo "🔨 Rebuilding admin container..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose up -d --build dashboard"

echo "🔄 Restarting stream containers..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose restart harvard-gpu fortwayne-gpu umass-gpu nsw-gpu"

echo ""
echo "✓ Code updated and containers restarted!"
echo "  Logs: ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose logs -f'"
