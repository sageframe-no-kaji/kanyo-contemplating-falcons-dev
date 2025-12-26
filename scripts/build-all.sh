#!/bin/bash
# Build and push CPU, VAAPI, and NVIDIA CUDA docker images to registry

set -e

# Image name and registry
IMAGE_NAME="kanyo-contemplating-falcons-dev"
REGISTRY="ghcr.io/sageframe-no-kaji"

# Get git commit info for versioning
GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "========================================="
echo "Building Docker Images"
echo "========================================="
echo "Registry: ${REGISTRY}"
echo "Image:    ${IMAGE_NAME}"
echo "Commit:   ${GIT_COMMIT}"
echo "Date:     ${BUILD_DATE}"
echo "========================================="
echo ""

# Build CPU image
echo "📦 Building CPU image..."
docker build \
    -f Dockerfile.cpu \
    -t ${REGISTRY}/${IMAGE_NAME}:cpu \
    -t ${REGISTRY}/${IMAGE_NAME}:cpu-${GIT_COMMIT} \
    -t ${REGISTRY}/${IMAGE_NAME}:latest \
    --build-arg BUILD_DATE="${BUILD_DATE}" \
    --build-arg GIT_COMMIT="${GIT_COMMIT}" \
    .

echo ""
echo "✅ CPU image built successfully!"
echo ""

# Build VAAPI image
echo "🖥️  Building VAAPI (Intel iGPU) image..."
docker build \
    -f Dockerfile.vaapi \
    -t ${REGISTRY}/${IMAGE_NAME}:vaapi \
    -t ${REGISTRY}/${IMAGE_NAME}:vaapi-${GIT_COMMIT} \
    --build-arg BUILD_DATE="${BUILD_DATE}" \
    --build-arg GIT_COMMIT="${GIT_COMMIT}" \
    .

echo ""
echo "✅ VAAPI image built successfully!"
echo ""

# Build NVIDIA image
echo "🎮 Building NVIDIA CUDA image..."
docker build \
    -f Dockerfile.nvidia \
    -t ${REGISTRY}/${IMAGE_NAME}:nvidia \
    -t ${REGISTRY}/${IMAGE_NAME}:nvidia-${GIT_COMMIT} \
    --build-arg BUILD_DATE="${BUILD_DATE}" \
    --VAAPI (Intel iGPU):"
echo "  ${REGISTRY}/${IMAGE_NAME}:vaapi"
echo "  ${REGISTRY}/${IMAGE_NAME}:vaapi-${GIT_COMMIT}"
echo ""
echo "build-arg GIT_COMMIT="${GIT_COMMIT}" \
    .

echo ""
echo "✅ NVIDIA image built successfully!"
echo ""
echo "========================================="
echo "Built Images"
echo "========================================="
echo "CPU:"
echo "  ${REGISTRY}/${IMAGE_NAME}:cpu"
echo "  ${REGISTRY}/${IMAGE_NAME}:cpu-${GIT_COMMIT}"
echo "  ${REGISTRY}/${IMAGE_NAME}:latest"
echo ""
echo "NVIDIA:"
echo "  ${REGISTRY}/${IMAGE_NAME}:nvidia"
echo "  ${REGISTRY}/${IMAGE_NAME}:nvidia-${GIT_COMMIT}"
echo "========================================="
echo ""

# Ask before pushing
read -p "Push images to registry? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
    echo "📤 Pushing CPU images..."
    docker push ${REGISTRY}/${IMAGE_NAME}:cpu
    docker push ${REGVAAPI images..."
    docker push ${REGISTRY}/${IMAGE_NAME}:vaapi
    docker push ${REGISTRY}/${IMAGE_NAME}:vaapi-${GIT_COMMIT}

    echo ""
    echo "📤 Pushing ISTRY}/${IMAGE_NAME}:cpu-${GIT_COMMIT}
    docker push ${REGISTRY}/${IMAGE_NAME}:latest

    echo ""
    echo "📤 Pushing NVIDIA images..."
    docker push ${REGISTRY}/${IMAGE_NAME}:nvidia
    docker push ${REGISTRY}/${IMAGE_NAME}:nvidia-${GIT_COMMIT}

    echo ""
    echo "VAAPI deployment:"
    echo "  docker compose -f docker-compose.vaapi.yml pull"
    echo "  docker compose -f docker-compose.vaapi.yml up -d"
    echo ""
    echo "✅ All images pushed to registry!"
    echo ""
    echo "========================================="
    echo "Deployment Commands"
    echo "========================================="
    echo "CPU deployment:"
    echo "  docker compose pull"
    echo "  docker compose up -d"
    echo ""
    echo "GPU deployment:"
    echo "  docker cvaapi.sh  # Intel iGPU"
    echo "  ./build-ompose -f docker-compose.nvidia.yml pull"
    echo "  docker compose -f docker-compose.nvidia.yml up -d"
    echo "========================================="
else
    echo ""
    echo "⏸️  Skipping push to registry"
    echo ""
    echo "To push manually:"
    echo "  ./build-cpu.sh    # CPU only"
    echo "  ./build-nvidia.sh # NVIDIA only"
fi
