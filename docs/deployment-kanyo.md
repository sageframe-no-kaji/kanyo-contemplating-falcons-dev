# Deployment Plan — kanyo.lan, v1.0.0

The full deployment plan for moving the production host (`kanyo`, reachable as
kanyo.lan on the LAN or via Tailscale) to image-based detector deployment at
v1.0.0, plus the accompanying viewer, dashboard, and per-site config updates.

**Scope of this deploy:**

- Detectors move from src-mounted `:nvidia` (stale image, 2026-04-21) to the
  pinned `1.0.0-nvidia` image. The src mount is removed.
- Deployed image is the **nvidia** flavor (`1.0.0-nvidia`). There is no bare
  `:1.0.0` tag — semver tags are flavor-suffixed.
- **yolov8n stays.** The yolov8s model upgrade is deferred to a later deploy;
  weights are baked into the image, so that will ship as a new image version.
- Admin dashboard remains host-built from the `kanyo-code` checkout.
- Viewer (separate repo/compose project) updated via its own git pull + build.
- Per-site config tuning for fortwayne and harvard; umass and nsw stay on
  template defaults.

All commands run from the Mac unless prefixed `# on host`. Host compose
commands need `sudo` (use `ssh -t` for interactive sudo).

---

## Phase 0 — Preflight

1. **Release exists.** After the deployment PR merges to main, tag and push:
   ```bash
   git tag v1.0.0 && git push origin v1.0.0
   ```
   Wait for the "Build and Publish Docker Image" workflow to finish, then
   confirm the tag is on GHCR:
   ```bash
   gh api "/orgs/sageframe-no-kaji/packages/container/kanyo-contemplating-falcons-dev/versions" \
     --jq '.[].metadata.container.tags[]' | grep '1.0.0-nvidia'
   ```
2. **Host reachable, fleet healthy before touching anything:**
   ```bash
   ssh kanyo 'cd /opt/services/kanyo-admin && sudo docker compose ps'
   ```
   Expect harvard, nsw, fortwayne, umass detectors + `kanyo-admin-web` all Up.
3. **Disk headroom** for a ~7 GB image pull:
   ```bash
   ssh kanyo 'df -h /var/lib/docker && df -h /opt/services'
   ```
4. **Snapshot the current state** (rollback anchors):
   ```bash
   ssh kanyo 'cd /opt/services/kanyo-admin && \
     sudo cp docker-compose.yml docker-compose.yml.pre-v1.0.0 && \
     sudo cp .env .env.pre-v1.0.0 && \
     cd /opt/services/kanyo-code && git rev-parse HEAD'
   ```
   Record the printed `kanyo-code` commit — it is the admin/dev rollback point.
5. **Note current per-site configs:**
   ```bash
   ssh kanyo 'for s in harvard nsw fortwayne umass; do \
     sudo cp /opt/services/kanyo-$s/config.yaml /opt/services/kanyo-$s/config.yaml.pre-v1.0.0; done'
   ```

---

## Phase 1 — Viewer (separate repo, own compose project)

The viewer is its own checkout and compose project at
`/opt/services/kanyo-viewer` and does not touch the detector fleet.

```bash
ssh kanyo 'cd /opt/services/kanyo-viewer && git pull'
ssh -t kanyo 'cd /opt/services/kanyo-viewer && sudo docker compose up -d --build'
```

Verify:

```bash
ssh kanyo 'curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/'   # expect 200
```

Spot-check https://kanyo.sageframe.net (via the Cloudflare tunnel) loads and
shows current clips.

---

## Phase 2 — kanyo-code pull + dashboard rebuild

The host checkout remains as the admin build source, `cookies.txt` home, and
ops-script carrier. Pull it to the release commit and rebuild the dashboard:

```bash
./ops/update-admin.sh          # git pull kanyo-code + rebuild dashboard container
```

Verify:

```bash
ssh kanyo 'curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5000/'   # expect 200/401 (basic auth)
ssh kanyo 'sudo docker logs kanyo-admin-web --tail 30'
```

Dashboard shows all four streams with correct status.

---

## Phase 3 — Per-site config edits

Edit on the host (`sudo`, files owned 1000:1000). Keys are documented in
`configs/config.template.yaml`. Only set the keys listed; leave the rest as-is.

**fortwayne** (`/opt/services/kanyo-fortwayne/config.yaml`):

```yaml
min_significant_seconds: 60
damping_arrivals_threshold: 6
```

**harvard** (`/opt/services/kanyo-harvard/config.yaml`):

```yaml
presence_sustain_confidence: 0.10
merge_window_seconds: 120
min_significant_seconds: 15
damping_arrivals_threshold: 10
```

**umass, nsw** — no edits; they run template defaults
(`presence_sustain_confidence: 0.15`, `merge_window_seconds: 300`,
`min_significant_seconds: 30`, `damping_arrivals_threshold: 8`).

Config changes take effect when containers are recreated in Phase 5 — no
separate restart needed.

---

## Phase 4 — Switch to the new compose template + pull image

1. **Install the new template** (from the repo's `docker/docker-compose.yml`;
   the old file was backed up in Phase 0):
   ```bash
   scp docker/docker-compose.yml kanyo:/tmp/docker-compose.yml
   ssh -t kanyo 'sudo mv /tmp/docker-compose.yml /opt/services/kanyo-admin/docker-compose.yml && \
     sudo chown 1000:1000 /opt/services/kanyo-admin/docker-compose.yml'
   ```
2. **Migrate `.env`** — keep the existing values (`TELEGRAM_BOT_TOKEN`,
   `KANYO_CODE_ROOT`, `KANYO_CAM{1,2,4,5,6}_ROOT`) and add the image pin:
   ```bash
   ssh kanyo 'cd /opt/services/kanyo-admin && \
     grep -q "^KANYO_IMAGE=" .env || \
     echo "KANYO_IMAGE=ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:1.0.0-nvidia" | sudo tee -a .env'
   ```
3. **Sanity-check the merged compose config** (fails loudly on missing vars):
   ```bash
   ssh kanyo 'cd /opt/services/kanyo-admin && sudo docker compose config --quiet && echo OK'
   ```
4. **Pull the release image** (big pull; detectors keep running on the old
   image until recreate):
   ```bash
   ssh -t kanyo 'cd /opt/services/kanyo-admin && sudo docker compose pull harvard-gpu'
   ```

---

## Phase 5 — Canary harvard, then fleet

Phases 4+5 are exactly what `./ops/update-code.sh 1.0.0-nvidia` automates
(minus the template copy). Run the script, or do it by hand:

```bash
ssh -t kanyo 'cd /opt/services/kanyo-admin && sudo docker compose up -d --force-recreate harvard-gpu'
ssh kanyo 'sudo docker logs kanyo-harvard-gpu --tail 200 -f'
```

### Canary log-verification checklist (harvard)

Watch the log until every box ticks. Give it at least one 5-minute summary
cycle (~6–7 minutes) before rolling the fleet.

- [ ] **Config loads clean** — config banner shows the Phase 3 values
      (`presence_sustain_confidence: 0.10`, `merge_window_seconds: 120`,
      `min_significant_seconds: 15`, `damping_arrivals_threshold: 10`);
      no unknown-key or validation warnings.
- [ ] **YOLO loads** — yolov8n model load line, CUDA device detected, no
      "CUDA not available / falling back to CPU" warning.
- [ ] **Stream connects** — yt-dlp resolves the Harvard stream and frames
      start flowing (no repeated 403s — that would be the IP-ban pattern).
- [ ] **Presence lines** — periodic presence/state-machine lines appear; no
      tracebacks.
- [ ] **5-min confidence summary** — the periodic confidence summary line
      arrives on schedule with plausible values.
- [ ] **Admin healthy** — dashboard still shows harvard Up and can read its
      logs/clips.
- [ ] **No src mount** — confirm the container is running baked code:
      ```bash
      ssh kanyo 'sudo docker inspect kanyo-harvard-gpu --format "{{range .Mounts}}{{.Destination}} {{end}}"'
      ```
      Must list `/app/cookies.txt /app/config.yaml /app/clips /app/logs` —
      **no `/app/src`**.

### Fleet

```bash
ssh -t kanyo 'cd /opt/services/kanyo-admin && sudo docker compose pull && \
  sudo docker compose up -d --force-recreate nsw-gpu fortwayne-gpu umass-gpu'
```

(bigbear stays defined-but-stopped; it is behind the `bigbear` compose profile.)

---

## Phase 6 — Post-deploy verification

```bash
ssh kanyo 'cd /opt/services/kanyo-admin && sudo docker compose ps'
```

- [ ] All four detectors + dashboard Up; no restart loops (`RESTARTING`/crash
      counts) after 10 minutes.
- [ ] Each detector passes the abbreviated canary checklist (config, YOLO,
      stream, presence lines) — `sudo docker logs kanyo-<site>-gpu --tail 100`.
- [ ] Running image is the pinned tag on every detector:
  ```bash
  ssh kanyo 'for s in harvard nsw fortwayne umass; do \
    sudo docker inspect kanyo-$s-gpu --format "$s: {{.Config.Image}}"; done'
  ```
- [ ] Clips still being written: check `clips/$(date +%Y-%m-%d)/` for new files
      after the next arrival (or within a few hours on active cams).
- [ ] Viewer (kanyo.sageframe.net) and admin (`:5000`) both serving.
- [ ] GPU in use: `ssh kanyo nvidia-smi` shows the python processes.

Leave the Phase 0 backups in place for at least a week of clean operation,
then remove the `*.pre-v1.0.0` files.

---

## Rollback

Each layer rolls back independently; use the smallest one that fixes the
problem.

**Detectors — image repoint** (seconds; old image is still on disk):

```bash
ssh kanyo 'cd /opt/services/kanyo-admin && \
  sudo sed -i "s|^KANYO_IMAGE=.*|KANYO_IMAGE=ghcr.io/sageframe-no-kaji/kanyo-contemplating-falcons-dev:nvidia|" .env'
ssh -t kanyo 'cd /opt/services/kanyo-admin && sudo docker compose up -d --force-recreate harvard-gpu nsw-gpu fortwayne-gpu umass-gpu'
```

**Detectors — full template rollback** (restores the src-mount world exactly
as it was):

```bash
ssh -t kanyo 'cd /opt/services/kanyo-admin && \
  sudo cp docker-compose.yml.pre-v1.0.0 docker-compose.yml && \
  sudo cp .env.pre-v1.0.0 .env && \
  sudo docker compose up -d --force-recreate'
```

**Admin dashboard** — pin `kanyo-code` back and rebuild:

```bash
ssh kanyo 'cd /opt/services/kanyo-code && git checkout <phase-0-commit>'
ssh -t kanyo 'cd /opt/services/kanyo-admin && sudo docker compose up -d --build dashboard'
```

**Viewer** — same pattern in its own project:

```bash
ssh kanyo 'cd /opt/services/kanyo-viewer && git checkout <previous-commit>'
ssh -t kanyo 'cd /opt/services/kanyo-viewer && sudo docker compose up -d --build'
```

**Per-site configs** — restore the Phase 0 copies:

```bash
ssh kanyo 'sudo cp /opt/services/kanyo-<site>/config.yaml.pre-v1.0.0 /opt/services/kanyo-<site>/config.yaml'
ssh -t kanyo 'cd /opt/services/kanyo-admin && sudo docker compose restart <site>-gpu'
```

**Behavioral rollback (no image change)** — if the new presence/significance
behavior misfires on a site, disable it per-site in that site's `config.yaml`
and restart just that container:

```yaml
presence_enabled: false             # restores legacy presence behavior
significance_filter_enabled: false  # restores unfiltered arrival/departure events
```

---

## Deferred / out of scope for this deploy

- **yolov8s** — model upgrade ships as a later image version (weights are
  baked at build).
- **bigbear** — stays defined-but-stopped behind the `bigbear` profile.
- **Admin dashboard image-based build** — dashboard stays host-built; moving
  it to a published image is a later decision.
- **CPU/VAAPI flavors** — production is nvidia-only.
