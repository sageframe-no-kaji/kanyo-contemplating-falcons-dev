#!/bin/bash
# Download from live stream - captures from DVR start if available
# For live streams, yt-dlp records in real-time. Use --live-from-start
# to grab the DVR buffer (usually 2-12 hours depending on stream settings).

TIMESTAMP=$(date +%Y%m%d_%H%M)
DURATION="${1:-21600}"  # Default 6 hours (21600 seconds), or pass as argument
OUTPUT="data/samples/falcon_${TIMESTAMP}.mp4"

echo "📥 Recording live stream..."
echo "Duration: $((DURATION / 3600)) hours ($DURATION seconds)"
echo "Output: $OUTPUT"
echo "Press Ctrl+C to stop early"
echo ""

# --live-from-start: Start from beginning of DVR buffer
# ffmpeg -t limits recording duration
yt-dlp \
  --live-from-start \
  --format "best[height<=720]" \
  --downloader ffmpeg \
  --downloader-args "ffmpeg:-t $DURATION" \
  --output "$OUTPUT" \
  "https://www.youtube.com/watch?v=glczTFRRAK4"

if [ -f "$OUTPUT" ]; then
  echo ""
  echo "✅ Download complete!"
  echo "File: $OUTPUT"
  echo "Size: $(du -h "$OUTPUT" | cut -f1)"
else
  echo "❌ Download failed or was interrupted"
fi
