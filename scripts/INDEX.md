# Scripts

Quick reference for all automation scripts.

## Build Scripts

- **`scripts/build-cpu.sh`** - Build and push CPU-only Docker image (pure CPU, no GPU acceleration)
- **`scripts/build-vaapi.sh`** - Build and push Intel iGPU Docker image (VAAPI + OpenVINO)
- **`scripts/build-nvidia.sh`** - Build and push NVIDIA GPU Docker image (CUDA 12.1)
- **`scripts/build-all.sh`** - Build and optionally push all three Docker variants (cpu, vaapi, nvidia)

## Deployment Scripts

- **`scripts/deploy-nvidia.sh`** - Initial deployment setup for NVIDIA GPU production server (creates directories, copies configs, deploys docker-compose)
- **`scripts/deploy-production.sh`** - Update production code and rebuild containers (pull code, down, up --build)
- **`scripts/update-code.sh`** - Quick production update (pull code, restart containers without rebuild)

## Utility Scripts

- **`scripts/download_sample.sh`** - Download sample video for testing falcon detection

## Directories

- **`scripts/data/`** - Captured clips and events from test runs
- **`scripts/sample/`** - Sample video files for testing
