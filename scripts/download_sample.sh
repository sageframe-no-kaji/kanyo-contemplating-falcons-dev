#!/bin/bash
# Download sample falcon cam footage for testing

echo "📥 Downloading sample falcon cam footage..."

# Create data directory if it doesn't exist
mkdir -p data/samples

# Download 2-minute clip
yt-dlp \
  --format "best[height<=720]" \
  --download-sections "*00:00:00-00:02:00" \
  --output "data/samples/falcon_sample.mp4" \
  "https://www.youtube.com/watch?v=glczTFRRAK4"

echo "✅ Sample downloaded to data/samples/falcon_sample.mp4"
echo "Duration: ~2 minutes"
echo "Resolution: 720p"
