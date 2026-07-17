# Ops Index

Operational scripts for deploying and managing Kanyo. Deployment scripts run
from the Mac and operate on the production host (`kanyo`, reachable as
kanyo.lan / via Tailscale) over SSH.

## Deployment

| Script | Purpose |
|--------|---------|
| `update-code.sh` | Deploy a pinned detector image to production: repoint `KANYO_IMAGE`, pull, canary harvard, then fleet. Usage: `./ops/update-code.sh 1.0.0-nvidia` |
| `update-admin.sh` | Update the admin dashboard: git pull `kanyo-code` on the host + rebuild the host-built dashboard container |

## Host Utilities

| Script | Purpose |
|--------|---------|
| `ban-watch.sh` | Runs on the host in tmux; polls YouTube to detect when an IP ban lifts, notifies via ntfy |
| `event-search.sh` | Search through event JSON files (see `event-search-README.md`) |

## Local Image Building (`build/`)

CI builds and publishes the `cpu` and `nvidia` images on push/tag; these are
for local builds only (`vaapi` is not CI-built).

| Script | Purpose |
|--------|---------|
| `build/build-cpu.sh` | Build CPU-only image locally |
| `build/build-nvidia.sh` | Build NVIDIA GPU image locally |
| `build/build-vaapi.sh` | Build Intel iGPU (VAAPI) image locally |

## Archive (`archive/`)

Superseded scripts, kept for reference. Do not use in production.

| Script | Superseded by |
|--------|---------------|
| `archive/update-code-gitpull.sh` | `update-code.sh` (image-based deploy replaced the git-pull + src-mount route at v1.0.0; git-pull remains a documented dev-only option) |
| `archive/build-all.sh` | CI (`.github/workflows/build.yml`) + `build/` scripts |
| `archive/deploy-nvidia.sh` | `docs/deployment-kanyo.md` + `update-code.sh` |
| `archive/deploy-production.sh` | `docs/deployment-kanyo.md` + `update-code.sh` |
| `archive/update-production.sh` | `update-code.sh` |

## Usage Examples

### Deploy a release to production (canary, then fleet)
```bash
./ops/update-code.sh 1.0.0-nvidia
```

### Update the admin dashboard
```bash
./ops/update-admin.sh
```

### Search for events
```bash
./ops/event-search.sh "ARRIVED" /opt/services/kanyo-harvard/clips
```

## See Also

- [docs/deployment-kanyo.md](../docs/deployment-kanyo.md) — production deploy plan
- [docker/DOCKER-DEPLOYMENT.md](../docker/DOCKER-DEPLOYMENT.md) — deployment guide
- [Quickstart.md](../Quickstart.md) — getting started
