#!/usr/bin/env python3
"""
Test live feed with tee mode for configurable duration.

Usage:
    python scripts/test_live_feed.py --nsw --duration 5
    python scripts/test_live_feed.py --harvard --duration 3
    python scripts/test_live_feed.py --config custom_config.yaml --duration 10
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kanyo.detection.realtime_monitor import RealtimeMonitor
from kanyo.utils.config import load_config
from kanyo.utils.logger import get_logger, setup_logging_from_config

logger = get_logger(__name__)


def main():
    """Run live feed test with specified configuration."""
    parser = argparse.ArgumentParser(
        description="Test live falcon stream with ffmpeg tee mode"
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to config file (e.g., test_config_nsw.yaml)",
    )
    parser.add_argument(
        "--harvard",
        action="store_true",
        help="Use Harvard falcon cam (test_config_harvard.yaml)",
    )
    parser.add_argument(
        "--nsw",
        action="store_true",
        help="Use NSW falcon cam (test_config_nsw.yaml)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=5,
        help="Test duration in minutes (default: 5)",
    )

    args = parser.parse_args()

    # Determine config file
    if args.harvard:
        config_path = "test_config_harvard.yaml"
        stream_name = "Harvard"
    elif args.nsw:
        config_path = "test_config_nsw.yaml"
        stream_name = "NSW"
    elif args.config:
        config_path = args.config
        stream_name = "Custom"
    else:
        print("Error: Must specify --harvard, --nsw, or --config")
        parser.print_help()
        sys.exit(1)

    # Check config file exists
    if not Path(config_path).exists():
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)

    # Override config path environment variable
    os.environ["KANYO_CONFIG_PATH"] = config_path

    # Load config
    config = load_config(config_path)
    setup_logging_from_config(config)

    logger.info("=" * 80)
    logger.info(f"LIVE FEED TEST - {stream_name} Falcon Cam")
    logger.info("=" * 80)
    logger.info(f"Config: {config_path}")
    logger.info(f"Stream: {config.get('video_source')}")
    logger.info(f"Duration: {args.duration} minutes")
    logger.info(f"Tee mode: {config.get('live_use_ffmpeg_tee', False)}")
    logger.info(f"Proxy URL: {config.get('live_proxy_url')}")
    logger.info(f"Buffer dir: {config.get('buffer_dir')}")
    logger.info(f"Chunk duration: {config.get('continuous_chunk_minutes')} minutes")
    logger.info("=" * 80)

    # Create test buffer directory
    buffer_dir = Path(config.get("buffer_dir", "/tmp/kanyo-test-buffer"))
    buffer_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Created buffer directory: {buffer_dir}")

    # Calculate end time
    duration_seconds = args.duration * 60
    start_time = datetime.now()
    end_time_target = start_time.timestamp() + duration_seconds

    try:
        # Create monitor
        monitor = RealtimeMonitor(
            stream_url=config.get("video_source"),
            confidence_threshold=config.get("detection_confidence", 0.5),
            exit_timeout_seconds=config.get("exit_timeout", 120),
            process_interval_frames=config.get("frame_interval", 30),
            detect_any_animal=config.get("detect_any_animal", True),
            animal_classes=config.get("animal_classes"),
            use_tee=config.get("live_use_ffmpeg_tee", False),
            proxy_url=config.get("live_proxy_url"),
            buffer_dir=config.get("buffer_dir"),
            chunk_minutes=config.get("continuous_chunk_minutes", 10),
            output_fps=config.get("clip_fps", 30),
        )

        logger.info("Starting monitor...")
        logger.info(f"Will run until: {datetime.fromtimestamp(end_time_target)}")

        # Connect to stream
        if not monitor.capture.connect():
            logger.error("Failed to connect to stream")
            sys.exit(1)

        logger.info("✓ Connected to stream successfully")
        logger.info("✓ Processing frames...")

        frame_count = 0
        detection_count = 0

        # Run for specified duration
        while time.time() < end_time_target:
            frame = monitor.capture.read_frame()

            if frame is None:
                logger.warning("No frame received, attempting reconnect...")
                if not monitor.capture.connect():
                    logger.error("Reconnection failed")
                    break
                continue

            frame_count += 1

            # Process every Nth frame
            if frame_count % monitor.process_interval == 0:
                # Extract numpy array from Frame object
                monitor.process_frame(frame.data)

                # Log progress every minute
                elapsed = time.time() - start_time.timestamp()
                if frame_count % (monitor.process_interval * 60) == 0:
                    remaining = (end_time_target - time.time()) / 60
                    logger.info(
                        f"Progress: {elapsed/60:.1f}min elapsed, "
                        f"{remaining:.1f}min remaining, "
                        f"{frame_count} frames processed"
                    )

        # Test complete
        elapsed = time.time() - start_time.timestamp()
        logger.info("=" * 80)
        logger.info("TEST COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Duration: {elapsed/60:.1f} minutes")
        logger.info(f"Frames processed: {frame_count}")
        logger.info(f"Detection checks: {frame_count // monitor.process_interval}")

        # Check segments created
        if monitor.capture.tee_manager:
            segments = monitor.capture.tee_manager.get_recent_segments()
            logger.info(f"Segments created: {len(segments)}")
            if segments:
                logger.info("Segment files:")
                for seg in segments:
                    size_mb = seg.stat().st_size / (1024 * 1024)
                    logger.info(f"  - {seg.name} ({size_mb:.1f} MB)")

        logger.info("=" * 80)

    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cleanup
        if "monitor" in locals():
            # Create final clip if falcon still present
            monitor.create_final_clip()
            monitor.capture.disconnect()
            logger.info("Disconnected from stream")


if __name__ == "__main__":
    main()
