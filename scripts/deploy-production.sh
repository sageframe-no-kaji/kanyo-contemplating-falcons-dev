#!/bin/bash
# Deploy latest code to production server

set -e

PROD_HOST="shingan.lan"
CODE_DIR="/opt/services/kanyo-code"
ADMIN_DIR="/opt/services/kanyo-admin"

echo "========================================="
echo "Kanyo Production Deployment"
echo "========================================="
echo ""
echo "Target: ${PROD_HOST}"
echo ""

# Step 1: Pull latest code
echo "→ Pulling latest code..."
ssh "${PROD_HOST}" "cd ${CODE_DIR} && git pull"
echo ""

# Step 2: Stop containers
echo "→ Stopping containers..."
ssh "${PROD_HOST}" "cd ${ADMIN_DIR} && docker compose down"
echo ""

# Step 3: Rebuild and start
echo "→ Building and starting containers..."
ssh "${PROD_HOST}" "cd ${ADMIN_DIR} && docker compose up -d --build"
echo ""

# Step 4: Verify
echo "→ Verifying deployment..."
ssh "${PROD_HOST}" "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'"
echo ""
echo "✅ Deployment complete!"
