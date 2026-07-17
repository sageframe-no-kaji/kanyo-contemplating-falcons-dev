#!/bin/bash
# Deploy a pinned detector image to production (image-based deployment).
#
# Repoints KANYO_IMAGE in the host's /opt/services/kanyo-admin/.env to the
# given tag, pulls it, then recreates the detector fleet with a canary first:
# harvard-gpu is recreated alone, you verify its logs, and only after
# confirmation does the rest of the fleet roll.
#
# Usage:
#   ./ops/update-code.sh <image-tag> [remote-host]
#   ./ops/update-code.sh 1.0.0-nvidia
#   ./ops/update-code.sh 1.1.0-nvidia kanyo
#
# Rollback is the same command with the previous tag.
#
# The legacy git-pull + restart route (src-mounted code) is archived at
# ops/archive/update-code-gitpull.sh and is dev-only now.

set -e

IMAGE_TAG="${1:-}"
REMOTE_HOST="${2:-kanyo}"
IMAGE_REPO="ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev"
ADMIN_DIR="/opt/services/kanyo-admin"
CANARY="harvard-gpu"
FLEET="nsw-gpu fortwayne-gpu umass-gpu"

if [ -z "${IMAGE_TAG}" ]; then
    echo "Usage: $0 <image-tag> [remote-host]"
    echo "Example: $0 1.0.0-nvidia"
    echo "Example: $0 1.1.0-nvidia kanyo"
    exit 1
fi

IMAGE="${IMAGE_REPO}:${IMAGE_TAG}"

echo "Deploying ${IMAGE} to ${REMOTE_HOST}..."
echo ""

echo "📌 Pinning KANYO_IMAGE in ${ADMIN_DIR}/.env..."
ssh "${REMOTE_HOST}" "cd ${ADMIN_DIR} && \
    if grep -q '^KANYO_IMAGE=' .env; then \
        sed -i 's|^KANYO_IMAGE=.*|KANYO_IMAGE=${IMAGE}|' .env; \
    else \
        echo 'KANYO_IMAGE=${IMAGE}' >> .env; \
    fi && grep '^KANYO_IMAGE=' .env"

echo "📥 Pulling image..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose pull ${CANARY}"

echo "🐤 Canary: recreating ${CANARY}..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose up -d --force-recreate ${CANARY}"

echo ""
echo "Canary is up. Verify before rolling the fleet:"
echo "  ssh ${REMOTE_HOST} 'sudo docker logs kanyo-harvard-gpu --tail 100 -f'"
echo "Checklist: config loads clean, YOLO loads, stream connects, presence"
echo "lines appearing, no tracebacks (see docs/deployment-kanyo.md)."
echo ""
read -p "Canary healthy — recreate the rest of the fleet (${FLEET})? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Stopping after canary. Fleet still on previous image."
    echo "Roll forward later:  ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose up -d --force-recreate ${FLEET}'"
    echo "Roll canary back:    $0 <previous-tag> ${REMOTE_HOST}"
    exit 0
fi

echo "🔄 Recreating fleet..."
ssh -t "${REMOTE_HOST}" "cd ${ADMIN_DIR} && sudo docker compose up -d --force-recreate ${FLEET}"

echo ""
echo "✓ Fleet deployed on ${IMAGE}!"
echo "  Status: ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose ps'"
echo "  Logs:   ssh ${REMOTE_HOST} 'cd ${ADMIN_DIR} && sudo docker compose logs -f'"
