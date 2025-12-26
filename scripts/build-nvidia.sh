#!/bin/bash
# Build and push NVIDIA CUDA docker image to registry

set -e

# Image name and registry
IMAGE_NAME="kanyo-contemplating-falcons-dev"
REGISTRY="ghcr.io/sageframe-no-kaji"
TAG="nvidia"

# Get git commit info for versioning
GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "Building NVIDIA CUDA image..."
docker build \
    -f docker/Dockerfile.nvidia \
    -t ${REGISTRY}/${IMAGE_NAME}:${TAG} \
    -t ${REGISTRY}/${IMAGE_NAME}:${TAG}-${GIT_COMMIT} \
    --build-arg BUILD_DATE="${BUILD_DATE}" \
    --build-arg GIT_COMMIT="${GIT_COMMIT}" \
    .

echo ""
echo "✓ Image built successfully!"
echo ""
echo "Tags created:"
echo "  ${REGISTRY}/${IMAGE_NAME}:${TAG}"
echo "  ${REGISTRY}/${IMAGE_NAME}:${TAG}-${GIT_COMMIT}"
echo ""
echo "Pushing to registry..."
docker push ${REGISTRY}/${IMAGE_NAME}:${TAG}
docker push ${REGISTRY}/${IMAGE_NAME}:${TAG}-${GIT_COMMIT}

echo ""
echo "✓ Images pushed to registry!"
echo ""
echo "To deploy on GPU machine:"
echo "  docker compose -f docker-compose.nvidia.yml pull"
echo "  docker compose -f docker-compose.nvidia.yml up -d"
echo "  docker compose -f docker-compose.nvidia.yml logs -f"
