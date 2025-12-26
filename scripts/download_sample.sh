#!/bin/bash
# Download sample falcon cam footage for testing

echo "📥 Downloading sample falcon cam footage..."

# Create sample directory if it doesn't exist
mkdir -p scripts/sample

# Download 2-minute clip
yt-dlp \
  --format "best[height<=720]" \
  --download-sections "*00:00:00-00:02:00" \
  --output "scripts/sample/falcon_sample.mp4" \
  "https://www.youtube.com/watch?v=glczTFRRAK4"

echo "✅ Sample downloaded to scripts/sample/falcon_sample.mp4"
echo "Duration: ~2 minutes"
echo "Resolution: 720p"
