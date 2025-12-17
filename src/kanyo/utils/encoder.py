"""
Hardware video encoder detection for ffmpeg.

Detects and validates available hardware encoders for video compression.
Used by clip extraction, continuous recording, and buffer management.
"""

from __future__ import annotations

import subprocess

from kanyo.utils.logger import get_logger

logger = get_logger(__name__)

# Hardware encoder detection cache
_detected_encoder: str | None = None


def detect_hardware_encoder(verbose: bool = False) -> str:
    """
    Detect available hardware encoder.

    Returns encoder name:
    - 'h264_videotoolbox' for Mac (VideoToolbox)
    - 'h264_nvenc' for NVIDIA GPU (requires nvidia-drivers)
    - 'h264_vaapi' for Intel/AMD on Linux (requires vaapi)
    - 'h264_qsv' for Intel QuickSync (requires intel-media-va-driver)
    - 'h264_amf' for AMD GPU on Windows
    - 'libx264' as fallback (software, always works)

    For Intel 630 on Debian:
        apt install vainfo intel-media-va-driver ffmpeg
        # Then use h264_vaapi or let auto-detect find it

    For NVIDIA P1000 on Debian:
        apt install nvidia-driver ffmpeg
        # Then use h264_nvenc or let auto-detect find it
    """
    global _detected_encoder
    if _detected_encoder is not None and not verbose:
        return _detected_encoder

    # Priority order for detection
    # VAAPI is preferred on Linux for Intel integrated GPUs
    encoders = [
        ("h264_videotoolbox", "VideoToolbox (Mac)"),
        ("h264_nvenc", "NVENC (NVIDIA GPU)"),
        ("h264_vaapi", "VAAPI (Intel/AMD Linux)"),
        ("h264_qsv", "QuickSync (Intel)"),
        ("h264_amf", "AMF (AMD Windows)"),
    ]

    if verbose:
        print("Checking available hardware encoders...")
        print()

    available = []

    for encoder, name in encoders:
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
            )
            if encoder not in result.stdout:
                if verbose:
                    print(f"  ❌ {encoder}: not available in ffmpeg")
                continue

            # Verify it actually works with a test encode
            # VAAPI needs -vaapi_device
            if encoder == "h264_vaapi":
                test_cmd = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-vaapi_device",
                    "/dev/dri/renderD128",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=64x64:d=1",
                    "-vf",
                    "format=nv12,hwupload",
                    "-c:v",
                    encoder,
                    "-f",
                    "null",
                    "-",
                ]
            else:
                test_cmd = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "nullsrc=s=64x64:d=1",
                    "-c:v",
                    encoder,
                    "-f",
                    "null",
                    "-",
                ]

            test = subprocess.run(test_cmd, capture_output=True, timeout=10)

            if test.returncode == 0:
                if verbose:
                    print(f"  ✅ {encoder}: {name}")
                available.append((encoder, name))
            else:
                if verbose:
                    print(f"  ⚠️  {encoder}: available but test failed")
        except subprocess.TimeoutExpired:
            if verbose:
                print(f"  ⚠️  {encoder}: timeout during test")
        except FileNotFoundError:
            if verbose:
                print("  ❌ ffmpeg not found!")
            break

    if verbose:
        print()
        print("  ℹ️  libx264: software encoder (always available)")
        print()

    if available:
        encoder, name = available[0]
        if not verbose:
            logger.info(f"Using hardware encoder: {name}")
        else:
            print(f"Selected: {encoder} ({name})")
        _detected_encoder = encoder
        return encoder

    logger.info("Using software encoder: libx264")
    _detected_encoder = "libx264"
    return "libx264"


if __name__ == "__main__":
    """CLI to test hardware encoder detection."""
    import platform

    print("=" * 60)
    print("Kanyo Clip Encoder Detection")
    print("=" * 60)
    print()

    encoder = detect_hardware_encoder(verbose=True)

    print()
    print("=" * 60)
    print()
    print("To set encoder in config.yaml:")
    print(f"  clip_encoder: {encoder}")
    print()
    print("Or use 'auto' to detect at runtime:")
    print("  clip_encoder: auto")
    print()

    # Setup instructions based on platform
    if platform.system() == "Linux":
        print("Linux setup for hardware encoding:")
        print()
        print("  Intel iGPU (e.g., Intel 630):")
        print("    apt install intel-media-va-driver vainfo ffmpeg")
        print("    vainfo  # verify VAAPI works")
        print()
        print("  NVIDIA GPU (e.g., P1000):")
        print("    apt install nvidia-driver ffmpeg")
        print("    nvidia-smi  # verify driver works")
        print()
    elif platform.system() == "Darwin":
        print("macOS: VideoToolbox is built-in, no setup needed.")
        print()
