#!/bin/bash
# Build and push CPU docker image to registry

set -e

# Image name and registry
IMAGE_NAME="kanyo-contemplating-falcons-dev"
REGISTRY="ghcr.io/sageframe-no-kaji"
TAG="cpu"

# Get git commit info for versioning
GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "Building CPU image..."
docker build \
    -f Dockerfile \
    -t ${REGISTRY}/${IMAGE_NAME}:${TAG} \
    -t ${REGISTRY}/${IMAGE_NAME}:${TAG}-${GIT_COMMIT} \
    -t ${REGISTRY}/${IMAGE_NAME}:latest \
    --build-arg BUILD_DATE="${BUILD_DATE}" \
    --build-arg GIT_COMMIT="${GIT_COMMIT}" \
    .

echo ""
echo "✓ Image built successfully!"
echo ""
echo "Tags created:"
echo "  ${REGISTRY}/${IMAGE_NAME}:${TAG}"
echo "  ${REGISTRY}/${IMAGE_NAME}:${TAG}-${GIT_COMMIT}"
echo "  ${REGISTRY}/${IMAGE_NAME}:latest"
echo ""
echo "Pushing to registry..."
docker push ${REGISTRY}/${IMAGE_NAME}:${TAG}
docker push ${REGISTRY}/${IMAGE_NAME}:${TAG}-${GIT_COMMIT}
docker push ${REGISTRY}/${IMAGE_NAME}:latest

echo ""
echo "✓ Images pushed to registry!"
echo ""
echo "To deploy:"
echo "  docker compose pull"
echo "  docker compose up -d"
echo "  docker compose logs -f"
