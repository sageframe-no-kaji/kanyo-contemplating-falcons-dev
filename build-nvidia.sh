#!/bin/bash
# Build and tag NVIDIA CUDA docker image

set -e

# Image name and tags
IMAGE_NAME="kanyo-contemplating-falcons"
REGISTRY="ghcr.io/sageframe-no-kaji"
TAG_BASE="nvidia"

# Get git commit info for versioning
GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Build the image
echo "Building NVIDIA CUDA image..."
docker build \
    -f Dockerfile.nvidia \
    -t ${IMAGE_NAME}:${TAG_BASE}-local \
    -t ${IMAGE_NAME}:${TAG_BASE}-${GIT_COMMIT} \
    --build-arg BUILD_DATE="${BUILD_DATE}" \
    --build-arg GIT_COMMIT="${GIT_COMMIT}" \
    --build-arg GIT_BRANCH="${GIT_BRANCH}" \
    .

echo ""
echo "Image built successfully!"
echo "Local tag: ${IMAGE_NAME}:${TAG_BASE}-local"
echo "Commit tag: ${IMAGE_NAME}:${TAG_BASE}-${GIT_COMMIT}"
echo ""
echo "To push to registry:"
echo "  docker tag ${IMAGE_NAME}:${TAG_BASE}-${GIT_COMMIT} ${REGISTRY}/${IMAGE_NAME}:${TAG_BASE}"
echo "  docker tag ${IMAGE_NAME}:${TAG_BASE}-${GIT_COMMIT} ${REGISTRY}/${IMAGE_NAME}:${TAG_BASE}-${GIT_COMMIT}"
echo "  docker push ${REGISTRY}/${IMAGE_NAME}:${TAG_BASE}"
echo "  docker push ${REGISTRY}/${IMAGE_NAME}:${TAG_BASE}-${GIT_COMMIT}"
echo ""
echo "To test locally with GPU:"
echo "  docker compose -f docker-compose.nvidia.yml up -d harvard-gpu"
echo "  docker compose -f docker-compose.nvidia.yml logs -f harvard-gpu"
