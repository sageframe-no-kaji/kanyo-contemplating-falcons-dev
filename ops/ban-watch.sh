#!/usr/bin/env bash
# scripts/ban-watch.sh
# Polls YouTube once every 30 minutes to detect when the IP ban lifts.
# Sends a notification via ntfy on transition from blocked -> unblocked.
# Deploy to kanyo.lan and run inside tmux — see deployment notes below.

set -u

VIDEO_ID="glczTFRRAK4"
NTFY_TOPIC="kanyo_admin_errors"
COMPOSE_DIR="/opt/services/kanyo-admin"
CHECK_INTERVAL_SECONDS=1800  # 30 min — DO NOT lower this
LOGFILE="$HOME/ban-watch.log"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOGFILE"
}

notify() {
  local title="$1"
  local message="$2"
  local priority="${3:-default}"
  curl -s \
    -H "Title: $title" \
    -H "Priority: $priority" \
    -d "$message" \
    "https://ntfy.sh/$NTFY_TOPIC" > /dev/null
}

check_status() {
  # Spins up a throwaway harvard-gpu container, resolves one HLS manifest,
  # fetches one segment, and returns the HTTP status code (or ERROR).
  # Total YouTube requests per call: 2 (manifest resolve + segment fetch).
  local result
  result=$(cd "$COMPOSE_DIR" && timeout 60 docker compose run --rm \
    --entrypoint bash harvard-gpu -c "
      URL=\$(yt-dlp --js-runtimes node -f 95 -g \
        'https://www.youtube.com/watch?v=$VIDEO_ID' 2>/dev/null)
      [ -z \"\$URL\" ] && echo ERROR && exit 1
      SEG=\$(curl -s \"\$URL\" | grep -E '^https' | head -1)
      [ -z \"\$SEG\" ] && echo ERROR && exit 1
      curl -s -o /dev/null -w '%{http_code}' -A 'Mozilla/5.0' \"\$SEG\"
    " 2>/dev/null)
  echo "${result:-ERROR}"
}

log "ban-watch starting (poll interval: ${CHECK_INTERVAL_SECONDS}s)"
notify "Kanyo ban-watch started" \
  "Polling YouTube every $((CHECK_INTERVAL_SECONDS / 60)) min. Will notify when IP ban lifts."

CONSECUTIVE_SUCCESSES=0
REQUIRED_SUCCESSES=2  # two 200s in a row before declaring victory
POLLS_SINCE_STATUS_NOTIFY=0
STATUS_NOTIFY_EVERY=4  # notify every 4 polls = every 2 hours

while true; do
  STATUS=$(check_status)
  log "check returned: $STATUS"

  if [ "$STATUS" = "200" ]; then
    CONSECUTIVE_SUCCESSES=$((CONSECUTIVE_SUCCESSES + 1))
    POLLS_SINCE_STATUS_NOTIFY=0
    log "consecutive successes: $CONSECUTIVE_SUCCESSES / $REQUIRED_SUCCESSES"
    if [ "$CONSECUTIVE_SUCCESSES" -ge "$REQUIRED_SUCCESSES" ]; then
      log "BAN LIFTED — notifying and exiting"
      notify "Kanyo IP unbanned" \
        "YouTube segments returned 200 twice in a row. Safe to deploy. Start containers with: cd /opt/services/kanyo-admin && docker compose up -d" \
        "high"
      exit 0
    fi
  else
    if [ "$CONSECUTIVE_SUCCESSES" -gt 0 ]; then
      log "false positive recovered — resetting counter"
    fi
    CONSECUTIVE_SUCCESSES=0
    POLLS_SINCE_STATUS_NOTIFY=$((POLLS_SINCE_STATUS_NOTIFY + 1))
    if [ "$POLLS_SINCE_STATUS_NOTIFY" -ge "$STATUS_NOTIFY_EVERY" ]; then
      notify "Kanyo still banned" \
        "Still getting 403 on YouTube segments. Watching. Next update in 2h." \
        "low"
      POLLS_SINCE_STATUS_NOTIFY=0
    fi
  fi

  sleep "$CHECK_INTERVAL_SECONDS"
done

# Deployment:
#   scp scripts/ban-watch.sh kanyo.lan:/opt/services/kanyo-admin/ban-watch.sh
#   ssh kanyo.lan "chmod +x /opt/services/kanyo-admin/ban-watch.sh && sudo touch /var/log/ban-watch.log && sudo chown atmarcus:atmarcus /var/log/ban-watch.log"
#   ssh kanyo.lan "tmux new -s ban-watch '/opt/services/kanyo-admin/ban-watch.sh'"
#   Detach: Ctrl+b d  |  Reattach: tmux attach -t ban-watch
