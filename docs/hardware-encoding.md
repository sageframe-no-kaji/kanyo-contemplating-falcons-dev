# Hardware Video Encoding

Kanyo can use hardware acceleration to encode video clips much faster (and with less CPU usage) than software encoding.

## Quick Start

In `config.yaml`:

```yaml
clip_hardware_encoding: true   # Use GPU if available, fallback to software
```

That's it! Kanyo auto-detects the best encoder for your system.

## Test Your System

Run this to see what encoders are available:

```bash
python -m kanyo.utils.encoder
```

Example output on Mac:
```
Checking available hardware encoders...

  ✅ h264_videotoolbox: VideoToolbox (Mac)
  ❌ h264_nvenc: not available in ffmpeg
  ...

Selected: h264_videotoolbox (VideoToolbox (Mac))
```

## Platform Setup

### macOS
No setup needed - VideoToolbox is built-in.

### Linux with Intel iGPU (e.g., Intel 630)

```bash
# Install VAAPI driver
sudo apt install intel-media-va-driver vainfo ffmpeg

# Verify it works
vainfo
```

You should see output mentioning VAProfileH264.

### Linux with NVIDIA GPU (e.g., P1000, RTX series)

```bash
# Install NVIDIA driver (includes NVENC)
sudo apt install nvidia-driver ffmpeg

# Verify driver is loaded
nvidia-smi
```

### Linux with AMD GPU

```bash
# AMD uses VAAPI on Linux
sudo apt install mesa-va-drivers vainfo ffmpeg

# Verify
vainfo
```

## Advanced: Force Specific Encoder

If auto-detection picks the wrong encoder, override it:

```yaml
clip_hardware_encoding: true
clip_encoder: h264_nvenc       # Force NVIDIA encoder
```

Available encoders:

| Encoder | Platform | Notes |
|---------|----------|-------|
| `h264_videotoolbox` | macOS | Built-in, fast |
| `h264_nvenc` | NVIDIA GPU | Requires nvidia-driver |
| `h264_vaapi` | Intel/AMD Linux | Requires vainfo + driver |
| `h264_qsv` | Intel (advanced) | Requires intel-media-va-driver-non-free |
| `libx264` | Any | Software, slow but always works |

## Quality Settings

```yaml
clip_crf: 23      # 18 = high quality (larger), 28 = lower quality (smaller)
clip_fps: 30      # 30fps is fine for watching, saves ~50% vs 60fps
```

## Troubleshooting

### "Encoder not available"
Run `python -m kanyo.generation.clips` to see what's detected. Install the appropriate driver (see Platform Setup above).

### Hardware encoding fails silently
Set `clip_hardware_encoding: false` to use software encoding as fallback.

### Clips are huge
Make sure `clip_compress: true` is set. If using `-c copy` mode (compress: false), clips aren't re-encoded.

## Performance Comparison

| Setting | 90s clip encode time | File size |
|---------|---------------------|-----------|
| Software (libx264) | ~45s | 31M |
| Hardware (VideoToolbox) | ~16s | 31M |
| No compression (-c copy) | ~1s | 400M+ |
