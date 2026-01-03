#!/usr/bin/env bash
#
# event-search
#
# Discover kanyo streams automatically, read timezone from config.yaml,
# and search logs by LOCAL time with DST-safe handling.
#
# Keeps:
#   - ERROR / INFO / WARNING / EVENT lines in window
#   - DEBUG lines ONLY if within ±5 lines of an EVENT
#
# Streams are discovered from:
#   /opt/services/kanyo-*/config.yaml
#   /opt/services/kanyo-*/logs/kanyo.log
#
# Usage:
#   event-search --list-streams
#   event-search <stream> <HH:MM> <window-min>
#   event-search <stream> <YYYY-MM-DD> <HH:MM> <window-min>
#   event-search                # interactive mode
#
# Examples:
#   event-search harvard 10:32 10
#   event-search nsw 2026-01-02 20:33 10

set -euo pipefail

ROOT="/opt/services"

die() {
  echo "Error: $*" >&2
  exit 1
}

############################
# STREAM DISCOVERY
############################

declare -A STREAM_TZ
declare -A STREAM_LOG

for cfg in "$ROOT"/kanyo-*/config.yaml; do
  [[ -f "$cfg" ]] || continue
  base=$(dirname "$cfg")
  stream=${base##*/kanyo-}

  log="$base/logs/kanyo.log"
  [[ -f "$log" ]] || continue

  tz=$(
    awk '
      /^[[:space:]]*timezone:[[:space:]]*/ {
        gsub(/^[^:]*:[[:space:]]*/, "", $0)
        print $0
        exit
      }
    ' "$cfg"
  )

  [[ -n "$tz" ]] || continue

  STREAM_TZ["$stream"]="$tz"
  STREAM_LOG["$stream"]="$log"
done

[[ ${#STREAM_TZ[@]} -gt 0 ]] || die "No valid kanyo streams found under $ROOT"

############################
# --list-streams
############################

if [[ "${1:-}" == "--list-streams" ]]; then
  printf "%-12s %s\n" "STREAM" "TIMEZONE"
  for s in "${!STREAM_TZ[@]}"; do
    printf "%-12s %s\n" "$s" "${STREAM_TZ[$s]}"
  done | sort
  exit 0
fi

############################
# ARGUMENT / INTERACTIVE MODE
############################

if [[ $# -eq 4 ]]; then
  STREAM="$1"
  TARGET_DATE="$2"
  TARGET_TIME="$3"
  WINDOW_MIN="$4"
elif [[ $# -eq 3 ]]; then
  STREAM="$1"
  TARGET_TIME="$2"
  WINDOW_MIN="$3"
  TARGET_DATE=""
else
  echo "Available streams:"
  i=1
  declare -a STREAM_LIST
  for s in "${!STREAM_TZ[@]}"; do
    printf "  %d) %-10s (%s)\n" "$i" "$s" "${STREAM_TZ[$s]}"
    STREAM_LIST[$i]="$s"
    ((i++))
  done
  echo

  read -r -p "Select stream [1-$((i-1))]: " sel
  STREAM="${STREAM_LIST[$sel]:-}"
  [[ -n "$STREAM" ]] || die "Invalid selection"

  read -r -p "Date (YYYY-MM-DD, blank = today): " TARGET_DATE
  read -r -p "Local time (HH:MM): " TARGET_TIME
  read -r -p "Window (minutes): " WINDOW_MIN
fi

[[ -n "${STREAM_TZ[$STREAM]:-}" ]] || die "Unknown stream: $STREAM"
[[ "$TARGET_TIME" =~ ^[0-9]{2}:[0-9]{2}$ ]] || die "Time must be HH:MM"
[[ "$WINDOW_MIN" =~ ^[0-9]+$ ]] || die "Window must be numeric minutes"

TZ_LOCAL="${STREAM_TZ[$STREAM]}"
LOG_FILE="${STREAM_LOG[$STREAM]}"

############################
# DATE DEFAULTING
############################

if [[ -z "$TARGET_DATE" ]]; then
  TARGET_DATE=$(TZ="$TZ_LOCAL" date +%Y-%m-%d)
fi

[[ "$TARGET_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "Date must be YYYY-MM-DD"

PREV_DATE=$(TZ="$TZ_LOCAL" date -d "$TARGET_DATE -1 day" +%Y-%m-%d)

############################
# TIME WINDOW
############################

IFS=: read -r H M <<< "$TARGET_TIME"
CENTER=$((10#$H*60 + 10#$M))
HALF=$((WINDOW_MIN / 2))

START_MIN=$((CENTER - HALF))
END_MIN=$((CENTER + HALF))

if (( START_MIN < 0 )); then
  START_MIN=$((START_MIN + 1440))
  CROSSES_MIDNIGHT=1
elif (( END_MIN >= 1440 )); then
  END_MIN=$((END_MIN - 1440))
  CROSSES_MIDNIGHT=1
else
  CROSSES_MIDNIGHT=0
fi

START_TIME=$(printf "%02d:%02d" $((START_MIN/60)) $((START_MIN%60)))
END_TIME=$(printf "%02d:%02d" $((END_MIN/60)) $((END_MIN%60)))

echo "Searching stream '$STREAM'"
echo "  Log file : $LOG_FILE"
echo "  Timezone : $TZ_LOCAL"
echo "  Window   : $TARGET_DATE $START_TIME → $END_TIME" >&2
[[ $CROSSES_MIDNIGHT -eq 1 ]] && echo "  (crosses midnight)" >&2

############################
# SEARCH (DST-safe + cached)
############################

gawk -v tz_local="$TZ_LOCAL" \
     -v target_date="$TARGET_DATE" \
     -v prev_date="$PREV_DATE" \
     -v start="$START_TIME" \
     -v end="$END_TIME" \
     -v cross="$CROSSES_MIDNIGHT" '
function in_window(d,t) {
  if (!cross)
    return (d == target_date && t >= start && t <= end)
  else
    return (
      (d == target_date && t >= start) ||
      (d == prev_date  && t <= end)
    )
}

{
  if (match($0, /^([0-9]{4}-[0-9]{2}-[0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) UTC/, a)) {
    utc = a[1] " " a[2] ":" a[3] ":" a[4]

    if (!(utc in cache)) {
      cmd = "TZ=" tz_local " date -d \"" utc " UTC\" +\"%Y-%m-%d %H:%M\" 2>/dev/null"
      cmd | getline cache[utc]
      close(cmd)
    }

    split(cache[utc], p, " ")
    local_date = p[1]
    local_time = p[2]

    if (in_window(local_date, local_time)) {
      lines[NR] = $0
      if (toupper($0) ~ /EVENT/) {
        for (i = NR-5; i <= NR+5; i++) keep[i] = 1
      }
    }
  }
}

END {
  for (i = 1; i <= NR; i++) {
    if (!(i in lines)) continue
    u = toupper(lines[i])

    if (u ~ /DEBUG/) {
      if (keep[i]) print lines[i]
    } else if (u ~ /(ERROR|INFO|WARN|WARNING|EVENT)/) {
      print lines[i]
    }
  }
}
' "$LOG_FILE"
