# Live YouTube Stream Capture with ffmpeg Tee

## Architecture

Single ffmpeg process reads YouTube live stream and outputs to TWO destinations simultaneously:

1. **Proxy** (copy codec) → low-latency feed for detection
2. **Segment recorder** (hardware encoder) → rolling buffer for fast clip extraction

```
YouTube Live → ffmpeg tee → [udp://127.0.0.1:12345 (proxy)]
                          → [/tmp/kanyo-buffer/*.mp4 (segments)]

Detection reads proxy → immediate frame analysis
Clip extraction reads segments → fast ffmpeg -ss clipping
```

## Why This Works Better

**vs In-Memory Buffer:**
- ffmpeg handles HLS/DASH reconnection
- Segments on disk = instant clip extraction without reinventing seek logic
- Detection decoupled from recording = easier monitoring/restart

**vs Separate Processes:**
- Single process = perfect sync, no drift
- Shared HLS reader = less network load
- Simpler lifecycle management

## Platform Setup

### macOS Development

**Hardware Encoder:** VideoToolbox (built-in)

```bash
# No setup needed - VideoToolbox works out of the box
python -m kanyo.utils.encoder
# Should show: h264_videotoolbox
```

### Debian Production (Intel UHD 630)

**Hardware Encoder:** VAAPI

```bash
# Install drivers and tools
sudo apt update
sudo apt install intel-media-va-driver vainfo ffmpeg

# Add user to video group
sudo usermod -aG video $USER
# Log out and back in

# Verify VAAPI works
vainfo
# Should show: VAProfileH264Main, VAProfileH264High, etc.

# Test encoder
python -m kanyo.utils.encoder
# Should show: h264_vaapi
```

**Verify device access:**
```bash
ls -l /dev/dri/renderD128
# Should be accessible by your user (via video group)
```

### Debian Production (NVIDIA P1000)

**Hardware Encoder:** NVENC

```bash
# Install NVIDIA drivers
sudo apt update
sudo apt install nvidia-driver ffmpeg

# Verify GPU detected
nvidia-smi
# Should show P1000 with driver version

# Test encoder
python -m kanyo.utils.encoder
# Should show: h264_nvenc
```

## Configuration

Add to `config.yaml`:

```yaml
# Live Stream Ingestion (YouTube)
live_use_ffmpeg_tee: true      # enable tee mode for YouTube
live_proxy_url: "udp://127.0.0.1:12345"  # local proxy
buffer_dir: "/tmp/kanyo-buffer"  # segment storage
continuous_chunk_minutes: 10   # 10-minute segments
```

## Manual Testing

### 1. Resolve YouTube URL

```bash
yt-dlp -f "best[height<=720]" -g "https://www.youtube.com/watch?v=glczTFRRAK4"
```

Copy the direct URL (starts with `https://manifest...`)

### 2. Test ffmpeg Tee Command

**macOS (VideoToolbox):**
```bash
ffmpeg -hide_banner -loglevel warning \
  -fflags nobuffer -flags low_delay -re \
  -i "<DIRECT_URL>" \
  -map 0:v -c:v copy -f mpegts udp://127.0.0.1:12345 \
  -map 0:v -c:v h264_videotoolbox -crf 23 -r 30 \
    -f segment -segment_time 600 -strftime 1 \
    /tmp/kanyo-buffer/segment_%Y%m%d_%H%M%S.mp4
```

**Debian Intel (VAAPI):**
```bash
ffmpeg -hide_banner -loglevel warning \
  -fflags nobuffer -flags low_delay -re \
  -i "<DIRECT_URL>" \
  -map 0:v -c:v copy -f mpegts udp://127.0.0.1:12345 \
  -map 0:v -vaapi_device /dev/dri/renderD128 \
    -vf "format=nv12,hwupload" -c:v h264_vaapi \
    -rc_mode vbr -b:v 4M -r 30 \
    -f segment -segment_time 600 -strftime 1 \
    /tmp/kanyo-buffer/segment_%Y%m%d_%H%M%S.mp4
```

**Debian NVIDIA (NVENC):**
```bash
ffmpeg -hide_banner -loglevel warning \
  -fflags nobuffer -flags low_delay -re \
  -i "<DIRECT_URL>" \
  -map 0:v -c:v copy -f mpegts udp://127.0.0.1:12345 \
  -map 0:v -c:v h264_nvenc -preset fast -b:v 4M -r 30 \
    -f segment -segment_time 600 -strftime 1 \
    /tmp/kanyo-buffer/segment_%Y%m%d_%H%M%S.mp4
```

### 3. Test Proxy Connection

In another terminal:

```bash
ffplay -fflags nobuffer -flags low_delay udp://127.0.0.1:12345
```

Should see live stream with ~2-3 second delay.

### 4. Verify Segments

```bash
ls -lth /tmp/kanyo-buffer/
# Should see segment_*.mp4 files growing
```

## Running with Kanyo

```bash
# Enable tee mode in config.yaml
live_use_ffmpeg_tee: true

# Run realtime monitor
PYTHONPATH=src python -m kanyo.detection.realtime_monitor
```

## Troubleshooting

### "Permission denied" for /dev/dri/renderD128

```bash
sudo usermod -aG video $USER
# Log out and back in
```

### VAAPI fails with "no VA display"

```bash
# Install drivers
sudo apt install intel-media-va-driver

# Verify
vainfo
```

### Segments not appearing

```bash
# Check buffer directory exists
mkdir -p /tmp/kanyo-buffer

# Check ffmpeg process running
ps aux | grep ffmpeg

# Check logs
tail -f logs/kanyo.log
```

### Proxy connection fails

```bash
# Test with ffplay first
ffplay -fflags nobuffer udp://127.0.0.1:12345

# If that works, check OpenCV can read it
python -c "import cv2; cap = cv2.VideoCapture('udp://127.0.0.1:12345'); print('OK' if cap.isOpened() else 'FAIL')"
```

## Performance Expectations

### Latency
- YouTube to proxy: ~2-3 seconds (HLS inherent delay)
- Detection response: immediate (reads proxy frames)
- Clip extraction: 5-10 seconds (reads recent segments)

### Resource Usage (Intel UHD 630)
- CPU: ~15-20% (mostly ffmpeg HLS parsing)
- GPU: ~10-15% (VAAPI encoding)
- Memory: ~300MB total
- Network: ~4-5 Mbps downstream

### Storage
- 10-minute segments at 4 Mbps = ~300MB per file
- Keep last 60 minutes = ~1.8GB rolling buffer
- Cleanup happens automatically via `tee_manager.cleanup_old_segments()`

## Production Deployment

### systemd Service

Create `/etc/systemd/system/kanyo.service`:

```ini
[Unit]
Description=Kanyo Falcon Detection
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=kanyo
WorkingDirectory=/opt/kanyo
Environment="PYTHONPATH=/opt/kanyo/src"
ExecStart=/opt/kanyo/venv/bin/python -m kanyo.detection.realtime_monitor
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable kanyo
sudo systemctl start kanyo
sudo journalctl -u kanyo -f
```

### Buffer Directory Management

Add to crontab for periodic cleanup:
```bash
# Clean segments older than 2 hours every 30 minutes
*/30 * * * * find /tmp/kanyo-buffer -name "segment_*.mp4" -mmin +120 -delete
```

Or rely on automatic cleanup in code (already implemented in FFmpegTeeManager).
