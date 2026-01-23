#!/usr/bin/env python3
"""
Migrate events to correct date files based on their timestamps.

This script fixes the issue where events were written to the wrong date file
due to using initialization-time date instead of event-time date.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def parse_event_date(event: dict[str, Any], timezone: ZoneInfo) -> str | None:
    """Extract date from event's start_time in stream timezone."""
    start_time = event.get("start_time")
    if not start_time:
        return None

    try:
        # Parse ISO format timestamp
        dt = datetime.fromisoformat(start_time)

        # If naive, assume UTC and convert to stream timezone
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))

        # Convert to stream timezone
        dt_local = dt.astimezone(timezone)

        return dt_local.strftime("%Y-%m-%d")
    except (ValueError, Exception) as e:
        print(f"⚠️  Failed to parse timestamp '{start_time}': {e}")
        return None


def migrate_events(clips_dir: Path, timezone_name: str, dry_run: bool = False) -> None:
    """
    Migrate events to correct date files.

    Args:
        clips_dir: Path to clips directory
        timezone_name: IANA timezone name (e.g., "Australia/Sydney")
        dry_run: If True, only report changes without making them
    """
    tz = ZoneInfo(timezone_name)

    # Find all events files
    events_files = sorted(clips_dir.glob("*/events_*.json"))

    if not events_files:
        print("❌ No events files found")
        return

    print(f"🔍 Found {len(events_files)} events files")
    print(f"📅 Using timezone: {timezone_name}")
    print(f"{'🔎 DRY RUN MODE' if dry_run else '✏️  WRITE MODE'}\n")

    # Track events to move
    moves: dict[str, list[dict[str, Any]]] = {}  # target_date -> [events]
    removes: dict[str, list[int]] = {}  # source_file -> [indices_to_remove]

    total_events = 0
    misplaced_events = 0

    # First pass: identify misplaced events
    for events_file in events_files:
        # Extract date from filename (e.g., events_2025-12-30.json -> 2025-12-30)
        file_date = events_file.stem.replace("events_", "")

        try:
            with open(events_file) as f:
                events = json.load(f)
        except json.JSONDecodeError:
            print(f"⚠️  Skipping corrupted file: {events_file}")
            continue

        total_events += len(events)

        # Check each event
        indices_to_remove = []
        for idx, event in enumerate(events):
            event_date = parse_event_date(event, tz)

            if event_date and event_date != file_date:
                misplaced_events += 1
                print(f"📍 Event in {file_date} belongs in {event_date}")
                print(f"   start_time: {event.get('start_time')}")

                # Track for moving
                if event_date not in moves:
                    moves[event_date] = []
                moves[event_date].append(event)
                indices_to_remove.append(idx)

        if indices_to_remove:
            removes[str(events_file)] = indices_to_remove

    print(f"\n📊 Summary:")
    print(f"   Total events scanned: {total_events}")
    print(f"   Misplaced events: {misplaced_events}")
    print(f"   Dates affected: {len(moves)}")

    if not misplaced_events:
        print("\n✅ No misplaced events found!")
        return

    if dry_run:
        print(f"\n🔎 DRY RUN: Would move {misplaced_events} events to correct dates")
        return

    # Second pass: remove from source files
    print(f"\n🗑️  Removing misplaced events from source files...")
    for source_file_str, indices in removes.items():
        source_file = Path(source_file_str)

        with open(source_file) as f:
            events = json.load(f)

        # Remove in reverse order to maintain indices
        for idx in sorted(indices, reverse=True):
            events.pop(idx)

        # Backup original
        backup_file = source_file.with_suffix(".json.bak")
        source_file.rename(backup_file)

        # Write updated file
        with open(source_file, "w") as f:
            json.dump(events, f, indent=2)

        print(f"   ✓ Updated {source_file.name} (removed {len(indices)} events)")

    # Third pass: add to target files
    print(f"\n📥 Adding events to correct date files...")
    for target_date, events_to_add in moves.items():
        target_dir = clips_dir / target_date
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"events_{target_date}.json"

        # Load existing events or start fresh
        if target_file.exists():
            with open(target_file) as f:
                existing_events = json.load(f)

            # Backup
            backup_file = target_file.with_suffix(".json.bak")
            target_file.rename(backup_file)
        else:
            existing_events = []

        # Add new events
        existing_events.extend(events_to_add)

        # Sort by start_time
        existing_events.sort(key=lambda e: e.get("start_time", ""))

        # Write
        with open(target_file, "w") as f:
            json.dump(existing_events, f, indent=2)

        print(f"   ✓ Updated {target_file.name} (added {len(events_to_add)} events)")

    print(f"\n✅ Migration complete! Moved {misplaced_events} events to correct dates")
    print(f"💾 Backups saved with .bak extension")


def main():
    """Main entry point."""
    if len(sys.argv) < 3:
        print("Usage: migrate_events_to_correct_dates.py <clips_dir> <timezone> [--dry-run]")
        print("\nExample:")
        print("  python migrate_events_to_correct_dates.py /data/harvard/clips Australia/Sydney")
        print(
            "  python migrate_events_to_correct_dates.py /data/harvard/clips Australia/Sydney --dry-run"
        )
        sys.exit(1)

    clips_dir = Path(sys.argv[1])
    timezone_name = sys.argv[2]
    dry_run = "--dry-run" in sys.argv

    if not clips_dir.exists():
        print(f"❌ Directory not found: {clips_dir}")
        sys.exit(1)

    try:
        migrate_events(clips_dir, timezone_name, dry_run)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
