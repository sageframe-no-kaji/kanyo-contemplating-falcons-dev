# Scripts Index

Utility scripts for building, deploying, and managing Kanyo.

## Docker Image Building

| Script | Purpose |
|--------|---------|
| `build-all.sh` | Build all Docker image variants (cpu, vaapi, nvidia) |
| `build-cpu.sh` | Build CPU-only image |
| `build-vaapi.sh` | Build Intel iGPU (VAAPI) image |
| `build-nvidia.sh` | Build NVIDIA GPU image |

## Deployment

| Script | Purpose |
|--------|---------|
| `deploy-nvidia.sh` | Full deployment with NVIDIA image to production |
| `deploy-production.sh` | Deploy to production server |
| `update-code.sh` | Quick code update via git pull + container restart |
| `update-admin.sh` | Update admin GUI on production |
| `update-production.sh` | Update production deployment |

## Utilities

| Script | Purpose |
|--------|---------|
| `event-search.sh` | Search through event JSON files (see `event-search-README.md`) |

## Usage Examples

### Build and push NVIDIA image
```bash
./scripts/build-nvidia.sh
```

### Quick code update (no image rebuild)
```bash
./scripts/update-code.sh shingan.lan
```

### Search for events
```bash
./scripts/event-search.sh "ARRIVED" /opt/services/kanyo-harvard/clips
```

## See Also

- [QUICKSTART.md](../QUICKSTART.md) — Getting started
- [docker/DOCKER-DEPLOYMENT.md](../docker/DOCKER-DEPLOYMENT.md) — Full deployment guide
