# Kanyo Docker Deployment Guide

How Kanyo deploys with Docker. **The deployment path is image-based**: detectors
run a pinned, semver-tagged image pulled from GHCR. The legacy source-mount
route is a development-only option, documented at the end.

For the full production deploy plan for kanyo.lan, see
[docs/deployment-kanyo.md](../docs/deployment-kanyo.md).

---

## Images and Tags

CI ([.github/workflows/build.yml](../.github/workflows/build.yml)) builds and
publishes to `ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev` on
every push to `main` and on every `v*` git tag.

| Tag | Built from | Meaning |
|-----|-----------|---------|
| `1.0.0-nvidia`, `1.0-nvidia` | `Dockerfile.nvidia` | Pinned release, NVIDIA GPU flavor — **what production runs** |
| `1.0.0-cpu`, `1.0-cpu` | `Dockerfile.cpu` | Pinned release, CPU flavor |
| `nvidia`, `cpu` | respective Dockerfile | Floating tip of `main` — do not pin production to these |
| `nvidia-<sha>`, `cpu-<sha>` | respective Dockerfile | Exact-commit builds, for forensic pinning |

Semver tags are always flavor-suffixed; there is no bare `:1.0.0` tag, so a
version tag can never be ambiguous about hardware flavor.

Model weights (`yolov8n`) are baked into the image at build time; `/app/models`
is not mounted. A model change is an image change and ships as a new version.

The `vaapi` (Intel iGPU) flavor exists as `Dockerfile.vaapi` but is not built
by CI; build it locally with `ops/build/build-vaapi.sh` if needed.

---

## Production Deployment (image-based)

### Layout

One compose project drives the whole fleet:

```
/opt/services/
├── kanyo-admin/               ← compose project (docker-compose.yml + .env)
├── kanyo-code/                ← host checkout: admin build source, cookies.txt, ops
├── kanyo-harvard/             ← per-site: config.yaml, clips/, logs/
├── kanyo-nsw/
├── kanyo-fortwayne/
├── kanyo-umass/
└── kanyo-bigbear/             ← defined in compose, not normally running
```

The canonical compose template is [docker/docker-compose.yml](docker-compose.yml)
in this repo; the live copy at `/opt/services/kanyo-admin/docker-compose.yml`
is a gitignored host copy of it. Edit in the repo, copy to the host.

The image is selected by `KANYO_IMAGE` in `/opt/services/kanyo-admin/.env`:

```bash
KANYO_IMAGE=ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:1.0.0-nvidia
TELEGRAM_BOT_TOKEN=...
KANYO_CODE_ROOT=/opt/services/kanyo-code
KANYO_CAM1_ROOT=/opt/services/kanyo-harvard
KANYO_CAM2_ROOT=/opt/services/kanyo-nsw
KANYO_CAM4_ROOT=/opt/services/kanyo-fortwayne
KANYO_CAM5_ROOT=/opt/services/kanyo-umass
KANYO_CAM6_ROOT=/opt/services/kanyo-bigbear
```

### Upgrade

Releases are cut by pushing a git tag (`git tag v1.1.0 && git push origin v1.1.0`);
CI publishes `1.1.0-nvidia` / `1.1.0-cpu`. Then, from the Mac:

```bash
./ops/update-code.sh 1.1.0-nvidia          # canary harvard, confirm, then fleet
```

Or manually on the host:

```bash
cd /opt/services/kanyo-admin
sed -i 's|^KANYO_IMAGE=.*|KANYO_IMAGE=ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:1.1.0-nvidia|' .env
sudo docker compose pull harvard-gpu
sudo docker compose up -d --force-recreate harvard-gpu     # canary
# verify logs (see deploy plan), then:
sudo docker compose pull && sudo docker compose up -d --force-recreate
```

`--force-recreate` matters: `restart` does not pick up a new image.

### Rollback

Rollback is the same move with the previous tag — repoint and recreate:

```bash
sed -i 's|^KANYO_IMAGE=.*|KANYO_IMAGE=ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:1.0.0-nvidia|' .env
sudo docker compose up -d --force-recreate
```

Previously-pulled images are still on the host, so rollback needs no pull and
completes in seconds.

### Admin dashboard (host-built, unchanged)

The dashboard is the one service still built on the host, from
`${KANYO_CODE_ROOT}/admin/web`. Update it with:

```bash
./ops/update-admin.sh          # git pull kanyo-code + rebuild dashboard
```

The `kanyo-code` checkout stays on the host for three reasons: it is the admin
dashboard build source, it holds the shared `cookies.txt`, and it carries the
`ops/` scripts. It is **not** mounted into the detector containers.

---

## Configuration

Each site mounts a single `config.yaml` read-only from its site directory.
All keys are documented in
[configs/config.template.yaml](../configs/config.template.yaml) — that file is
the config-key reference; this guide does not duplicate it.

Config changes do not require a new image: edit
`/opt/services/kanyo-<site>/config.yaml` and `docker compose restart <site>-gpu`.

---

## Hardware Variants

| Flavor | Hardware | Dockerfile |
|--------|----------|------------|
| `nvidia` | NVIDIA GPU | `Dockerfile.nvidia` — production |
| `cpu` | Any CPU | `Dockerfile.cpu` |
| `vaapi` | Intel iGPU | `Dockerfile.vaapi` — not CI-built |

For CPU-only, remove the `deploy.resources.reservations` block from the
compose anchor. For VAAPI, additionally add:

```yaml
devices:
  - /dev/dri:/dev/dri
```

---

## Operations

```bash
cd /opt/services/kanyo-admin

sudo docker compose ps                          # fleet status
sudo docker compose up -d                       # start all
sudo docker compose restart harvard-gpu         # restart one site (same image)
sudo docker logs kanyo-harvard-gpu --tail 100 -f
docker image inspect --format '{{index .RepoTags}}' \
  $(docker inspect --format '{{.Image}}' kanyo-harvard-gpu)   # what's running?
```

Health checks:

```bash
sudo docker compose ps                                        # everything Up?
ls -la /opt/services/kanyo-harvard/clips/$(date +%Y-%m-%d)/   # clips flowing?
du -sh /opt/services/kanyo-*/clips/                           # disk
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5000/   # admin up?
```

---

## Development Workflow (dev-only route)

For rapid iteration on a **development** host, the legacy source-mount route is
still available: mount `src/` from a checkout over the baked image code, then
`git pull` + `docker compose restart` picks up changes in ~10 seconds.

The exact override block is documented (commented out) at the bottom of
[docker-compose.yml](docker-compose.yml). Enable it per-service by adding:

```yaml
volumes:
  - ${KANYO_CODE_ROOT}/src:/app/src:ro     # DEV ONLY
```

Do not run production this way. With a src mount, the image tag no longer
tells you what code is running, upgrades and rollbacks stop being atomic, and
a host-side `git pull` can silently change production behavior. Production
moved to pinned images at v1.0.0 precisely to close that gap.

When developing, rebuild the image (rather than mounting) when any of these
change: `requirements*.txt`, the Dockerfiles, or the model download step.

```bash
docker build -f docker/Dockerfile.nvidia -t kanyo:nvidia-dev .
```

---

## Troubleshooting

### Container won't start

```bash
sudo docker logs kanyo-<site>-gpu --tail 100
```

- **"No such file or directory"** — a volume path in `.env` doesn't exist on the host
- **"Permission denied"** — site dirs must be owned `1000:1000` (`sudo chown -R 1000:1000 /opt/services/kanyo-<site>`)
- **"could not select device driver nvidia"** — NVIDIA Container Toolkit missing/broken; verify with `docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi`

### YouTube stream fails

yt-dlp is upgraded at image build time. If YouTube changes break stream capture,
a floating-tip image (`:nvidia`) or a new release usually carries the fixed
yt-dlp. Persistent 403s across all streams usually mean an IP ban — see
`ops/ban-watch.sh`.

### High memory usage

`shm_size: '2gb'` is required for YOLO. If you see OOM errors, raise it to
`'4gb'` in the anchor.

---

## See Also

- [docs/deployment-kanyo.md](../docs/deployment-kanyo.md) — production deploy plan (kanyo.lan)
- [docs/docker-architecture.md](../docs/docker-architecture.md) — what runs where on the host
- [docs/adding-streams.md](../docs/adding-streams.md) — adding a stream
- [configs/config.template.yaml](../configs/config.template.yaml) — config-key reference
- [Quickstart.md](../Quickstart.md) — single-stream quick start
