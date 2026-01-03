#!/usr/bin/env bash
#
# event-search
#
# Search kanyo logs by LOCAL time, with DST-safe timezone handling.
# Keeps:
#   - ERROR / INFO / WARNING / EVENT lines in window
#   - DEBUG only if within ±5 lines of an EVENT
#
# Usage:
#   event-search harvard 10:32 10              # today in stream's local timezone
#   event-search nsw 2026-01-02 20:33 10       # specific date
#   event-search                                # interactive mode
#
# Easily extensible: just add entries to LOC_TZ[] below.

set -euo pipefail

############################
# CONFIGURATION
############################

# Location → Timezone mapping
declare -A LOC_TZ=(
  [nsw]="Australia/Sydney"
  [harvard]="America/New_York"
)

# Base log path pattern
LOG_BASE="/opt/services"

############################
# FUNCTIONS
############################

die() {
  echo "Error: $*" >&2
  exit 1
}

prompt() {
  read -r -p "$1: " "$2"
}

############################
# ARGUMENT / INTERACTIVE MODE
############################

if [[ $# -eq 4 ]]; then
  # With date: event-search nsw 2026-01-02 20:33 10
  LOCATION="$1"
  TARGET_DATE="$2"
  TARGET_TIME="$3"
  WINDOW_MIN="$4"
elif [[ $# -eq 3 ]]; then
  # Without date (use today in stream's timezone): event-search harvard 10:32 10
  LOCATION="$1"
  TARGET_TIME="$2"
  WINDOW_MIN="$3"
  TARGET_DATE=""  # Will be set to today below
  TARGET_DATE=""  # Will be set to today below
else
  echo "Available locations:"
  for k in "${!LOC_TZ[@]}"; do echo "  - $k"; done
  echo

  prompt "Location" LOCATION
  prompt "Date (YYYY-MM-DD, or blank for today)" TARGET_DATE
  prompt "Local time (HH:MM)" TARGET_TIME
  prompt "Window (minutes)" WINDOW_MIN
fi

[[ -n "${LOC_TZ[$LOCATION]:-}" ]] || die "Unknown location: $LOCATION"
[[ "$TARGET_TIME" =~ ^[0-9]{2}:[0-9]{2}$ ]] || die "Time must be HH:MM"
[[ "$WINDOW_MIN" =~ ^[0-9]+$ ]] || die "Window must be minutes (number)"

TZ_LOCAL="${LOC_TZ[$LOCATION]}"
LOG_FILE="${LOG_BASE}/kanyo-${LOCATION}/logs/kanyo.log"

[[ -f "$LOG_FILE" ]] || die "Log file not found: $LOG_FILE"

# Default to today in stream's local timezone if no date provided
if [[ -z "$TARGET_DATE" ]]; then
  TARGET_DATE=$(TZ="$TZ_LOCAL" date +%Y-%m-%d)
fi

[[ "$TARGET_DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "Date must be YYYY-MM-DD"

############################
# TIME WINDOW CALCULATION
############################

IFS=: read -r H M <<< "$TARGET_TIME"
START_MIN=$((10#$H*60 + 10#$M - WINDOW_MIN/2))
END_MIN=$((10#$H*60 + 10#$M + WINDOW_MIN/2))

START_MIN=$((START_MIN < 0 ? 0 : START_MIN))
END_MIN=$((END_MIN > 1439 ? 1439 : END_MIN))

START_TIME=$(printf "%02d:%02d" $((START_MIN/60)) $((START_MIN%60)))
END_TIME=$(printf "%02d:%02d" $((END_MIN/60)) $((END_MIN%60)))

echo "Searching: $TARGET_DATE $START_TIME to $END_TIME (local time in $TZ_LOCAL)" >&2

############################
# SEARCH (DST-safe using external date command)
############################

gawk -v target_date="$TARGET_DATE" \
     -v start_time="$START_TIME" \
     -v end_time="$END_TIME" \
     -v tz_local="$TZ_LOCAL" '
function in_window(dt, tm, targ_date, start_tm, end_tm) {
  if (dt != targ_date) return 0
  return (tm >= start_tm && tm <= end_tm)
}

{
  # Match UTC timestamp: 2026-01-02 23:15:30 UTC
  if (match($0, /^([0-9]{4}-[0-9]{2}-[0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) UTC/, a)) {
    utc_datetime = a[1] " " a[2] ":" a[3] ":" a[4]

    # Convert UTC to local time using date command (DST-safe)
    cmd = "TZ=" tz_local " date -d \"" utc_datetime " UTC\" +\"%Y-%m-%d %H:%M\" 2>/dev/null"
    cmd | getline local_datetime
    close(cmd)

    split(local_datetime, parts, " ")
    local_date = parts[1]
    local_time = parts[2]

    if (in_window(local_date, local_time, target_date, start_time, end_time)) {
      lines[NR] = $0
      # Mark EVENT lines and ±5 lines around them
      if (toupper($0) ~ /EVENT/) {
        for (i = NR - 5; i <= NR + 5; i++) keep[i] = 1
      }
    }
  }
}

END {
  for (i = 1; i <= NR; i++) {
    if (!(i in lines)) continue
    u = toupper(lines[i])

    # Keep DEBUG only if within ±5 of EVENT
    if (u ~ /DEBUG/) {
      if (keep[i]) print lines[i]
    }
    # Always keep ERROR, INFO, WARNING, EVENT
    else if (u ~ /(ERROR|INFO|WARN|WARNING|EVENT)/) {
      print lines[i]
    }
  }
}
' "$LOG_FILE"
