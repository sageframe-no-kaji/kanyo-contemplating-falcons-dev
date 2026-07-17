# Kanyo — Docker Container Architecture

Describes how the Docker services are organized across the two repos and what each container does on `kanyo.lan`.

---

## Repos Involved

| Repo | Purpose |
|------|---------|
| `kanyo-contemplating-falcons-dev` | Detection engine, admin dashboard, stream configs |
| `kanyo-viewer` | Public-facing web frontend (separate repo and compose project) |

---

## Directory Layout on `kanyo.lan`

```
/opt/services/
├── kanyo-admin/               ← main compose project (detection + admin dashboard)
│   ├── docker-compose.yml
│   └── ban-watch.sh           ← IP ban detection script (runs in tmux, not Docker)
│
├── kanyo-code/                ← source checkout — admin build source + ops scripts
│   ├── src/                   ← NOT mounted into containers (image-based deploy)
│   └── cookies.txt            ← shared YouTube auth cookies for all streams
│
├── kanyo-harvard/             ← stream data (ZFS dataset)
│   ├── config.yaml
│   ├── clips/
│   ├── data/
│   └── logs/
├── kanyo-nsw/                 ← stream data (ZFS dataset)
│   ├── config.yaml
│   ├── clips/
│   ├── data/
│   └── logs/
├── kanyo-fortwayne/           ← stream data
├── kanyo-umass/               ← stream data
├── kanyo-bigbear/             ← stream data
│
├── kanyo-cloudflared/         ← Cloudflare tunnel (own compose project)
│   ├── docker-compose.yml
│   └── config/
│
├── kanyo-mandala/             ← Baserow (own compose project, unrelated to detection)
│   ├── docker-compose.yml
│   └── data/
│
└── kanyo-viewer/              ← viewer repo checkout + compose project
    └── docker-compose.yml
```

---

## Services

### Detection streams (4 running, bigbear defined-but-stopped)

**Containers:** `kanyo-harvard-gpu`, `kanyo-nsw-gpu`, `kanyo-fortwayne-gpu`, `kanyo-umass-gpu` (+ `kanyo-bigbear-gpu`, defined behind the `bigbear` compose profile, not normally running)  
**Image:** `ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:<version>-nvidia` (pinned semver, selected by `KANYO_IMAGE` in `.env`)  
**Managed by:** `kanyo-admin/docker-compose.yml` (host copy of [docker/docker-compose.yml](../docker/docker-compose.yml))

All stream containers are defined in one compose file using a YAML anchor (`x-kanyo-gpu-service`) so they share the same GPU config, resource limits, and logging settings. Only the container name and data volume paths differ per stream.

**Deployment is image-based** — the detectors run the code baked into the pinned release image; no source is mounted. Upgrades and rollbacks are image-tag repoints (see [docker/DOCKER-DEPLOYMENT.md](../docker/DOCKER-DEPLOYMENT.md)). A live `src/` mount from `kanyo-code` remains available as a dev-only override, documented in the compose template.

**`cookies.txt` is shared** — a single file at `kanyo-code/cookies.txt` is mounted into all stream containers. Used for YouTube streams that require authenticated access.

```yaml
x-kanyo-gpu-service: &kanyo-gpu-service
  image: ${KANYO_IMAGE:-ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:1.0.0-nvidia}

services:
  harvard-gpu:
    <<: *kanyo-gpu-service
    container_name: kanyo-harvard-gpu
    volumes:
      - ${KANYO_CODE_ROOT}/cookies.txt:/app/cookies.txt:rw
      - ${KANYO_CAM1_ROOT}/config.yaml:/app/config.yaml:ro
      - ${KANYO_CAM1_ROOT}/clips:/app/clips
      - ${KANYO_CAM1_ROOT}/logs:/app/logs
  # ... nsw, fortwayne, umass, bigbear follow the same pattern
```

What each container does: connects to a YouTube live stream via `yt-dlp`, runs every Nth frame through YOLOv8 for bird detection, maintains a state machine (ABSENT → VISITING → ROOSTING), and writes arrival/departure clip `.mp4` and thumbnail `.jpg` files into `clips/YYYY-MM-DD/`.

---

### Admin dashboard

**Container:** `kanyo-admin-web`  
**Built from:** `kanyo-code/admin/web/` (FastAPI + Jinja2)  
**Port:** `5000`  
**Managed by:** `kanyo-admin/docker-compose.yml`

FastAPI app providing a web UI for monitoring and managing all streams from one place. Mounts the Docker socket (with explicit `group_add` for the docker group) so it can inspect and restart stream containers.

```yaml
dashboard:
  build: ${KANYO_CODE_ROOT}/admin/web
  container_name: kanyo-admin-web
  group_add:
    - "988"                            # docker group — needed for socket access
  ports:
    - "5000:5000"
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
    - ${KANYO_CAM1_ROOT}:/data/harvard
    - ${KANYO_CAM2_ROOT}:/data/nsw
    - ${KANYO_CAM4_ROOT}:/data/fortwayne
    - ${KANYO_CAM5_ROOT}:/data/umass
    - ${KANYO_CAM6_ROOT}:/data/bigbear
```

Capabilities: container status (up/down/uptime), browse clips and logs per stream, read stream configs, restart containers. Protected by HTTP Basic Auth.

---

### ban-watch.sh (not a Docker service)

A shell script in `kanyo-admin/` that runs in a `tmux` session on the host. When YouTube bans the server's IP, stream containers start failing. This script polls YouTube every 30 minutes using a throwaway `docker compose run` of the harvard container to check whether segments return HTTP 200. When two consecutive checks succeed, it sends an ntfy push notification and exits — signalling that it's safe to bring stream containers back up.

Not part of the compose stack. Deployed manually and attached via `tmux attach -t ban-watch`.

---

### Public web viewer

**Container:** `kanyo-viewer`  
**Repo:** `kanyo-viewer` (separate repo and compose project)  
**Port:** `3000`  
**Managed by:** `kanyo-viewer/docker-compose.yml`

React + FastAPI app serving the public site. Mounts `/opt/services` read-only and auto-discovers active streams by scanning for subdirectories containing `config.yaml`. The detection containers write clips; the viewer reads them — the two compose projects share only the filesystem.

```yaml
viewer:
  build: .
  container_name: kanyo-viewer
  ports:
    - "3000:3000"
  volumes:
    - /opt/services:/data:ro
  environment:
    - KANYO_ENV=production
    - ADMIN_API_URL=http://172.17.0.1:5000
```

The build is multi-stage: Node 18 compiles the React frontend (Vite), then a Python 3.11 image copies the built `dist/` and runs uvicorn on port 3000. FastAPI serves both the API and the SPA. Stores visit analytics in a local `analytics.db` SQLite file.

---

### Cloudflare tunnel

**Container:** `cloudflared-kanyo`  
**Image:** `cloudflare/cloudflared:latest`  
**Managed by:** `kanyo-cloudflared/docker-compose.yml`

Routes public internet traffic to the viewer on port 3000 without exposing any firewall ports. Uses `network_mode: host` so it can reach the viewer on localhost. Config (tunnel credentials) lives in `kanyo-cloudflared/config/`.

```yaml
cloudflared:
  image: cloudflare/cloudflared:latest
  command: tunnel run
  network_mode: host
  volumes:
    - ./config:/home/nonroot/.cloudflared/
```

---

### kanyo-mandala (Baserow)

**Container:** `kanyo-mandala`  
**Image:** `baserow/baserow:latest`  
**Port:** `8888`  
**URL:** `https://mandala.sageframe.net`

A Baserow instance (no-code database / spreadsheet tool). Separate from the detection pipeline — its own compose project with its own data volume. Not related to Kanyo detection logic.

---

## Data Flow

```
YouTube live stream
        │
        ▼
[kanyo-{stream}-gpu]  ←── config.yaml    (stream settings)
        │              ←── cookies.txt   (shared from kanyo-code/)
        │              (code baked into pinned image — no src mount)
        │
        ▼
/opt/services/kanyo-{stream}/
└── clips/YYYY-MM-DD/
    ├── events_YYYY-MM-DD.json
    ├── falcon_HHMMSS_arrival.mp4
    └── falcon_HHMMSS_arrival.jpg

        │ (read-only: /opt/services:/data:ro)    │ (docker.sock)
        ▼                                         ▼
[kanyo-viewer]                           [kanyo-admin-web]
  port 3000                                port 5000
  public site                              internal admin UI

        │
        ▼
[cloudflared-kanyo]
  public internet → kanyo.sageframe.net
```

---

## Port Summary

| Port | Container | Access |
|------|-----------|--------|
| 3000 | kanyo-viewer | Public (via Cloudflare tunnel) |
| 5000 | kanyo-admin-web | Internal only |
| 8888 | kanyo-mandala (Baserow) | Internal / mandala.sageframe.net |

---

## Adding a Stream

1. Create the stream directory: `mkdir -p /opt/services/kanyo-{name}/{clips,logs}`
2. Write `config.yaml` from the template in `kanyo-code/configs/config.template.yaml`
3. Add `KANYO_CAM{N}_ROOT=/opt/services/kanyo-{name}` to `kanyo-admin/.env`
4. Add a service block to `kanyo-admin/docker-compose.yml` (copy an existing stream block)
5. Add the stream volume to the `dashboard` service volumes
6. Start: `docker compose up -d {name}-gpu`

See [adding-streams.md](adding-streams.md) for full detail.
