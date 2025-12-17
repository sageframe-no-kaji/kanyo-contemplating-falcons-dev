# Ho 2: Falcon Vision

## Implementing Basic Falcon Detection with YOLOv8

**Duration:** 2-2.5 hours  
**Goal:** Build working falcon detection that processes video and identifies birds  
**Deliverable:** Detection script that outputs timestamped falcon sightings

---

## Python Primer for Ho 2 (15 minutes - Read This First!)

### Concepts You'll Use in This Ho

#### 1. Working with Video Files (OpenCV)

**What is OpenCV?** 📦 **LIBRARY** - Computer vision library for image/video processing

**Basic pattern:**

```python
import cv2

# Open a video file
video = cv2.VideoCapture("video.mp4")
# "Capture" = a video reader object

# Read frames one at a time
while True:
    success, frame = video.read()
    # success = True if we got a frame
    # frame = the image (as a numpy array)
    
    if not success:
        break  # End of video
    
    # Do something with frame
    print(f"Frame shape: {frame.shape}")  # e.g., (1080, 1920, 3)

video.release()  # Close the video
```

**Key concepts:**

- Video = sequence of images (frames)
- `VideoCapture` = opens video file
- `.read()` = get next frame
- Frame = numpy array (rows, columns, colors)

---

#### 2. Working with ML Models (YOLOv8)

**What is YOLO?** 📦 **LIBRARY** - "You Only Look Once" - fast object detection

**How it works (simplified):**

```
Image → YOLO Model → List of detected objects
                     Each object has:
                     - Class (bird, person, car, etc.)
                     - Confidence (0.0 to 1.0)
                     - Bounding box (x, y, width, height)
```

**Basic pattern:**

```python
from ultralytics import YOLO

# Load pre-trained model
model = YOLO("yolov8n.pt")
# "n" = nano (smallest, fastest)
# Model was trained on 80 common objects (including birds!)

# Run detection on an image
results = model(frame, conf=0.5)
# conf = minimum confidence threshold
# Returns list of detections

# Get detections
for result in results:
    boxes = result.boxes  # All detected objects
    for box in boxes:
        cls = int(box.cls[0])      # Class ID (14 = bird in COCO)
        conf = float(box.conf[0])   # Confidence (0.0-1.0)
        x, y, w, h = box.xywh[0]    # Position and size
```

**Key concepts:**

- Model = trained AI that recognizes objects
- Confidence = how sure the model is (0.5 = 50%)
- Bounding box = rectangle around detected object
- Class = what type of object (bird, person, etc.)

---

#### 3. Lists and Dictionaries

**Lists = ordered collection:**

```python
detections = []  # Empty list

# Add items
detections.append({"time": "10:30", "confidence": 0.8})
detections.append({"time": "10:35", "confidence": 0.9})

# Access by index
first = detections[0]  # {"time": "10:30", "confidence": 0.8}

# Loop through
for detection in detections:
    print(detection["time"])
```

**Dictionaries = key-value pairs:**

```python
detection = {
    "timestamp": "2024-12-15T10:30:00",
    "confidence": 0.87,
    "bbox": [100, 200, 50, 75]
}

# Access by key
when = detection["timestamp"]
conf = detection["confidence"]

# Add new key
detection["frame_number"] = 1234
```

---

#### 4. File Paths (pathlib)

**Path objects = modern way to work with files:**

```python
from pathlib import Path

# Create path object
video_path = Path("data/test_video.mp4")

# Check if exists
if video_path.exists():
    print("File found!")

# Get parts
video_path.parent        # Path("data")
video_path.name          # "test_video.mp4"
video_path.stem          # "test_video"
video_path.suffix        # ".mp4"

# Create new path
output_path = Path("output") / "results.json"
# Same as: Path("output/results.json")
```

---

#### 5. JSON (Saving Data)

**JSON = text format for storing structured data:**

```python
import json

# Python data
data = {
    "detections": [
        {"time": "10:30", "confidence": 0.8},
        {"time": "10:35", "confidence": 0.9}
    ],
    "total": 2
}

# Save to file
with open("results.json", "w") as f:
    json.dump(data, f, indent=2)
# "w" = write mode
# indent=2 = pretty formatting

# Load from file
with open("results.json", "r") as f:
    loaded_data = json.load(f)
# "r" = read mode
```

---

## Why This Ho Matters

**Before Ho 2:** You have infrastructure (config, logging, tests)  
**After Ho 2:** You can actually detect falcons in video!

**This is YOUR domain logic** - Tier 2 understanding required. You need to know:

- How detection works
- How to modify confidence thresholds
- How to debug when it doesn't work
- How to add features

**This is the core of Kanyo.**

---

## Part 1: Download Sample Video (15 minutes)

### Overview

We need test video to develop with. We'll download a short clip from the Memorial Hall falcon cam to test our detection without processing the entire live stream.

**🔧 BOOTSTRAP** - Writing a download script  
**📦 LIBRARY** - Using yt-dlp

---

### Create Download Script

**📁 File:** `download_sample.sh`  
**📂 Location:** `scripts/`  
**Full path:** `~/vaults/dev/kanyo-dev/scripts/download_sample.sh`

**What this does:** Downloads a 2-minute clip from the falcon cam for testing

```bash
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
```

**Create the file:**

```bash
cd ~/vaults/dev/kanyo-dev
code scripts/download_sample.sh
```

**Make it executable:**

```bash
chmod +x scripts/download_sample.sh
```

**Run it:**

```bash
./scripts/download_sample.sh
```

**What happens:**

- Creates `data/samples/` directory
- Downloads 2 minutes of video
- Saves as `falcon_sample.mp4`
- Takes 1-2 minutes depending on connection

**Understanding the command:**

- `--format "best[height<=720]"` - Download 720p quality (smaller file)
- `--download-sections "*00:00:00-00:02:00"` - Only first 2 minutes
- `--output` - Where to save it

---

## Part 2: Basic Detection Script (30 minutes)

### Overview

Now we'll create the core detection function. This reads a video file, runs YOLOv8 on each frame, and returns a list of timestamps where birds were detected.

**🎯 YOUR DOMAIN LOGIC** - This is the heart of Kanyo  
**📦 LIBRARY** - YOLOv8, OpenCV

---

### Understanding What We're Building

**Input:** Video file (`falcon_sample.mp4`)  
**Process:**

1. Open video with OpenCV
2. Read frame by frame
3. Run YOLOv8 detection on each frame
4. Filter for birds (class 14 in COCO dataset)
5. Record timestamp when bird detected

**Output:** List of detections with timestamps and confidence

---

### Create Detection Module

**📁 File:** `detect.py`  
**📂 Location:** `src/kanyo/detection/`  
**Full path:** `~/vaults/dev/kanyo-dev/src/kanyo/detection/detect.py`

**What this does:** Core falcon detection logic

**Open in VSCode:**

```bash
cd ~/vaults/dev/kanyo-dev
code src/kanyo/detection/detect.py
```

**Paste this code:**

```python
"""
Falcon detection using YOLOv8.

This module processes video files and detects birds (specifically falcons)
using the YOLOv8 object detection model.
"""

import cv2
from pathlib import Path
from typing import List, Dict, Any
from ultralytics import YOLO

from kanyo.utils.logger import get_logger
from kanyo.utils.config import load_config

logger = get_logger(__name__)

# COCO dataset class IDs
# YOLOv8 is trained on COCO which has 80 object classes
# Class 14 = bird (includes all birds, not just falcons)
BIRD_CLASS_ID = 14


def detect_falcons(
    video_path: str | Path,
    confidence: float = 0.5,
    frame_interval: int = 30,
    model_path: str = "models/yolov8n.pt"
) -> List[Dict[str, Any]]:
    """
    Detect falcons in a video file.
    
    Args:
        video_path: Path to video file
        confidence: Minimum confidence threshold (0.0-1.0)
        frame_interval: Process every Nth frame (30 = ~1 per second for 30fps video)
        model_path: Path to YOLOv8 model weights
    
    Returns:
        List of detections, each containing:
        - timestamp: Time in video (seconds)
        - frame_number: Which frame this was
        - confidence: Detection confidence (0.0-1.0)
        - bbox: Bounding box [x, y, width, height]
        - bird_count: Number of birds detected in this frame
    
    Example:
        >>> detections = detect_falcons("data/samples/falcon_sample.mp4")
        >>> print(f"Found {len(detections)} detections")
        >>> print(f"First detection at {detections[0]['timestamp']:.1f} seconds")
    """
    video_path = Path(video_path)
    
    # Validate video file exists
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    logger.info(f"Starting detection on {video_path.name}")
    logger.info(f"Confidence threshold: {confidence}")
    logger.info(f"Processing every {frame_interval} frames")
    
    # Load YOLOv8 model
    logger.info(f"Loading model: {model_path}")
    model = YOLO(model_path)
    
    # Open video
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")
    
    # Get video properties
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps
    
    logger.info(f"Video properties: {total_frames} frames, {fps:.1f} fps, {duration:.1f}s duration")
    
    detections = []
    frame_number = 0
    
    # Process video frame by frame
    while cap.isOpened():
        success, frame = cap.read()
        
        if not success:
            break  # End of video
        
        # Only process every Nth frame
        if frame_number % frame_interval == 0:
            # Calculate timestamp
            timestamp = frame_number / fps
            
            # Run detection
            results = model(frame, conf=confidence, verbose=False)
            # verbose=False suppresses per-frame output
            
            # Extract bird detections
            birds_in_frame = []
            for result in results:
                boxes = result.boxes
                for box in boxes:
                    cls = int(box.cls[0])
                    
                    # Only keep birds (class 14)
                    if cls == BIRD_CLASS_ID:
                        conf = float(box.conf[0])
                        xywh = box.xywh[0].tolist()  # Convert tensor to list
                        
                        birds_in_frame.append({
                            "confidence": conf,
                            "bbox": xywh  # [x_center, y_center, width, height]
                        })
            
            # If we detected any birds in this frame, record it
            if birds_in_frame:
                detection = {
                    "timestamp": timestamp,
                    "frame_number": frame_number,
                    "bird_count": len(birds_in_frame),
                    "birds": birds_in_frame,
                    "confidence_avg": sum(b["confidence"] for b in birds_in_frame) / len(birds_in_frame)
                }
                detections.append(detection)
                
                logger.debug(
                    f"Frame {frame_number} ({timestamp:.1f}s): "
                    f"Detected {len(birds_in_frame)} bird(s)"
                )
        
        frame_number += 1
    
    cap.release()
    
    logger.info(f"Detection complete: {len(detections)} frames with birds detected")
    logger.info(f"Processed {frame_number} total frames")
    
    return detections


def save_detections(detections: List[Dict[str, Any]], output_path: str | Path) -> None:
    """
    Save detections to JSON file.
    
    Args:
        detections: List of detection dictionaries
        output_path: Where to save JSON file
    """
    import json
    
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w") as f:
        json.dump(detections, f, indent=2)
    
    logger.info(f"Saved {len(detections)} detections to {output_path}")
```

---

### Understanding the Code (Key Parts Explained)

**Function signature:**

```python
def detect_falcons(
    video_path: str | Path,
    confidence: float = 0.5,
    ...
) -> List[Dict[str, Any]]:
```

- `video_path: str | Path` = accepts string OR Path object
- `confidence: float = 0.5` = default value 0.5 if not specified
- `-> List[Dict[str, Any]]` = returns list of dictionaries

**Loading the model:**

```python
model = YOLO(model_path)
```

📦 **LIBRARY** - YOLOv8 handles model loading, we just use it

**Opening video:**

```python
cap = cv2.VideoCapture(str(video_path))
fps = cap.get(cv2.CAP_PROP_FPS)
```

📦 **LIBRARY** - OpenCV reads video properties

**The main loop:**

```python
while cap.isOpened():
    success, frame = cap.read()
    if not success:
        break
    
    # Only process every Nth frame
    if frame_number % frame_interval == 0:
        # Run detection...
```

🎯 **YOUR LOGIC** - This pattern YOU need to understand:

- Read frame
- Check if we should process it (every 30th frame)
- Run detection
- Store results

**Running detection:**

```python
results = model(frame, conf=confidence, verbose=False)
```

📦 **LIBRARY** - YOLOv8 does the hard work

**Filtering for birds:**

```python
for box in boxes:
    cls = int(box.cls[0])
    if cls == BIRD_CLASS_ID:  # 14 = bird
        # Keep this detection
```

🎯 **YOUR LOGIC** - YOU decide to filter for birds

---

## Part 3: Test Detection Script (20 minutes)

### Overview

Let's create a simple script to run detection on our sample video and see it work!

**🔧 BOOTSTRAP** - Entry point script

---

### Create Test Script

**📁 File:** `run_detection.py`  
**📂 Location:** `scripts/`  
**Full path:** `~/vaults/dev/kanyo-dev/scripts/run_detection.py`

**What this does:** Runs detection on sample video and prints results

```python
"""
Run falcon detection on a video file.

Usage:
    python scripts/run_detection.py
    python scripts/run_detection.py --video data/samples/custom.mp4
    python scripts/run_detection.py --confidence 0.7
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import argparse
from kanyo.detection.detect import detect_falcons, save_detections
from kanyo.utils.logger import setup_logging, get_logger
from kanyo.utils.config import load_config

# Setup logging
setup_logging(level="INFO")
logger = get_logger(__name__)


def main():
    """Run detection on video file"""
    parser = argparse.ArgumentParser(description="Detect falcons in video")
    parser.add_argument(
        "--video",
        type=str,
        default="data/samples/falcon_sample.mp4",
        help="Path to video file"
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.5,
        help="Detection confidence threshold (0.0-1.0)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/detections.json",
        help="Output JSON file"
    )
    
    args = parser.parse_args()
    
    # Load config (for model path, etc.)
    config = load_config()
    
    logger.info("=" * 60)
    logger.info("Kanyo Falcon Detection")
    logger.info("=" * 60)
    
    # Run detection
    try:
        detections = detect_falcons(
            video_path=args.video,
            confidence=args.confidence,
            model_path=config.get("model_path", "models/yolov8n.pt")
        )
        
        # Print summary
        logger.info("=" * 60)
        logger.info("Detection Summary")
        logger.info("=" * 60)
        logger.info(f"Total detections: {len(detections)}")
        
        if detections:
            logger.info(f"First detection: {detections[0]['timestamp']:.1f}s")
            logger.info(f"Last detection: {detections[-1]['timestamp']:.1f}s")
            
            total_birds = sum(d['bird_count'] for d in detections)
            avg_confidence = sum(d['confidence_avg'] for d in detections) / len(detections)
            
            logger.info(f"Total bird observations: {total_birds}")
            logger.info(f"Average confidence: {avg_confidence:.2f}")
            
            # Show first 5 detections
            logger.info("\nFirst 5 detections:")
            for i, det in enumerate(detections[:5], 1):
                logger.info(
                    f"  {i}. Time: {det['timestamp']:.1f}s, "
                    f"Birds: {det['bird_count']}, "
                    f"Confidence: {det['confidence_avg']:.2f}"
                )
        else:
            logger.warning("No birds detected! Try lowering confidence threshold.")
        
        # Save results
        save_detections(detections, args.output)
        
        logger.info("=" * 60)
        logger.info("✅ Detection complete!")
        logger.info("=" * 60)
        
    except FileNotFoundError as e:
        logger.error(f"❌ Error: {e}")
        logger.info("Did you download the sample video?")
        logger.info("Run: ./scripts/download_sample.sh")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Error during detection: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Create the file:**

```bash
cd ~/vaults/dev/kanyo-dev
code scripts/run_detection.py
```

---

### Download Model and Run Detection

**First, make sure you have the sample video:**

```bash
cd ~/vaults/dev/kanyo-dev
ls data/samples/falcon_sample.mp4
# If file not found, run: ./scripts/download_sample.sh
```

**Run detection:**

```bash
python scripts/run_detection.py
```

**What happens:**

1. Downloads YOLOv8 nano model (~6 MB) on first run
2. Processes your sample video
3. Detects birds in each frame
4. Prints summary
5. Saves results to `data/detections.json`

**Expected output:**

```
============================================================
Kanyo Falcon Detection
============================================================
2024-12-15 15:30:00 | INFO     | kanyo.detection.detect | Starting detection on falcon_sample.mp4
2024-12-15 15:30:00 | INFO     | kanyo.detection.detect | Loading model: models/yolov8n.pt
2024-12-15 15:30:02 | INFO     | kanyo.detection.detect | Video properties: 3600 frames, 30.0 fps, 120.0s duration
2024-12-15 15:30:15 | INFO     | kanyo.detection.detect | Detection complete: 47 frames with birds detected
============================================================
Detection Summary
============================================================
2024-12-15 15:30:15 | INFO     | __main__ | Total detections: 47
2024-12-15 15:30:15 | INFO     | __main__ | First detection: 2.3s
2024-12-15 15:30:15 | INFO     | __main__ | Last detection: 118.7s
```

**If you see detections:** ✅ Success! Detection is working!

**If you see 0 detections:**

- Video might not have visible birds
- Try lowering confidence: `python scripts/run_detection.py --confidence 0.3`
- Or use a different video sample

---

## Part 4: Visualize Detections (Optional - 20 minutes)

### Overview

Let's create a script that draws bounding boxes on detected frames so you can SEE what the model is detecting.

**🔧 BOOTSTRAP** - Visualization script  
**📦 LIBRARY** - OpenCV drawing functions

---

### Create Visualization Script

**📁 File:** `visualize_detections.py`  
**📂 Location:** `scripts/`  
**Full path:** `~/vaults/dev/kanyo-dev/scripts/visualize_detections.py`

**What this does:** Creates images showing detected birds with bounding boxes

```python
"""
Visualize falcon detections with bounding boxes.

Creates images showing detected birds outlined with boxes.
Useful for debugging and seeing what the model detects.

Usage:
    python scripts/visualize_detections.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import cv2
import json
from kanyo.utils.logger import get_logger

logger = get_logger(__name__)


def visualize_detections(
    video_path: str,
    detections_path: str,
    output_dir: str = "data/visualizations",
    max_images: int = 10
):
    """
    Create visualization images with bounding boxes.
    
    Args:
        video_path: Path to video file
        detections_path: Path to detections JSON
        output_dir: Where to save images
        max_images: Maximum number of images to create
    """
    # Load detections
    with open(detections_path, "r") as f:
        detections = json.load(f)
    
    if not detections:
        logger.warning("No detections to visualize!")
        return
    
    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Open video
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    logger.info(f"Creating visualizations for {min(len(detections), max_images)} detections")
    
    # Process first N detections
    for i, detection in enumerate(detections[:max_images]):
        frame_number = detection["frame_number"]
        timestamp = detection["timestamp"]
        
        # Seek to frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        success, frame = cap.read()
        
        if not success:
            continue
        
        # Draw bounding boxes for each bird
        for bird in detection["birds"]:
            bbox = bird["bbox"]  # [x_center, y_center, width, height]
            conf = bird["confidence"]
            
            # Convert center coordinates to corner coordinates
            x_center, y_center, width, height = bbox
            x1 = int(x_center - width / 2)
            y1 = int(y_center - height / 2)
            x2 = int(x_center + width / 2)
            y2 = int(y_center + height / 2)
            
            # Draw rectangle
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            
            # Add label
            label = f"Bird {conf:.2f}"
            cv2.putText(
                frame, label, (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2
            )
        
        # Add timestamp
        time_label = f"Time: {timestamp:.1f}s | Birds: {detection['bird_count']}"
        cv2.putText(
            frame, time_label, (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2
        )
        
        # Save image
        output_file = output_path / f"detection_{i+1:03d}_{timestamp:.1f}s.jpg"
        cv2.imwrite(str(output_file), frame)
        logger.info(f"Saved: {output_file.name}")
    
    cap.release()
    logger.info(f"✅ Created {min(len(detections), max_images)} visualization images in {output_dir}")


if __name__ == "__main__":
    visualize_detections(
        video_path="data/samples/falcon_sample.mp4",
        detections_path="data/detections.json"
    )
```

**Create and run it:**

```bash
cd ~/vaults/dev/kanyo-dev
code scripts/visualize_detections.py

# Run it
python scripts/visualize_detections.py

# Check the results
ls data/visualizations/
```

**What you'll see:**

- Images in `data/visualizations/`
- Each image shows detected birds with green boxes
- Confidence scores labeled
- Timestamp displayed

**Open the images to verify detection is working!**

---

## Part 5: Add Tests for Detection (15 minutes)

### Overview

Let's write tests that verify our detection code works correctly.

**🔧 BOOTSTRAP** - Writing tests

---

### Update Test File

**📁 File:** `test_detection.py`  
**📂 Location:** `tests/`  
**Full path:** `~/vaults/dev/kanyo-dev/tests/test_detection.py`

**What this does:** Tests detection functionality

**Open the file and REPLACE the placeholder tests:**

```bash
cd ~/vaults/dev/kanyo-dev
code tests/test_detection.py
```

**Find this section:**

```python
@pytest.mark.skip(reason="Not implemented yet")
def test_falcon_detection():
    """Test falcon detection on sample frame"""
    pass
```

**Replace with:**

```python
def test_falcon_detection():
    """Test falcon detection on sample video"""
    from kanyo.detection.detect import detect_falcons
    from pathlib import Path
    
    # Check if sample video exists
    video_path = Path("data/samples/falcon_sample.mp4")
    
    if not video_path.exists():
        pytest.skip("Sample video not downloaded")
    
    # Run detection
    detections = detect_falcons(
        video_path=video_path,
        confidence=0.5,
        frame_interval=60  # Process fewer frames for faster testing
    )
    
    # Verify structure
    assert isinstance(detections, list)
    
    # If we got detections, verify structure
    if detections:
        first = detections[0]
        assert "timestamp" in first
        assert "frame_number" in first
        assert "bird_count" in first
        assert "birds" in first
        assert first["bird_count"] > 0


def test_detection_with_high_confidence():
    """Test that high confidence returns fewer detections"""
    from kanyo.detection.detect import detect_falcons
    from pathlib import Path
    
    video_path = Path("data/samples/falcon_sample.mp4")
    
    if not video_path.exists():
        pytest.skip("Sample video not downloaded")
    
    # Low confidence
    low_conf = detect_falcons(video_path, confidence=0.3, frame_interval=60)
    
    # High confidence
    high_conf = detect_falcons(video_path, confidence=0.7, frame_interval=60)
    
    # High confidence should have fewer or equal detections
    assert len(high_conf) <= len(low_conf)
```

**Run tests:**

```bash
pytest tests/test_detection.py -v
```

**Expected output:**

```
tests/test_detection.py::test_imports PASSED
tests/test_detection.py::test_config_loads PASSED
tests/test_detection.py::test_falcon_detection PASSED
tests/test_detection.py::test_detection_with_high_confidence PASSED
tests/test_detection.py::test_event_detection SKIPPED
```

---

## Part 6: Create Devlog Entry (10 minutes)

### Overview

Document what you learned and built in Ho 2.

---

### Create Devlog

**📁 File:** `ho-02-falcon-vision.md`  
**📂 Location:** `devlog/`  
**Full path:** `~/vaults/dev/kanyo-dev/devlog/ho-02-falcon-vision.md`

```markdown
# Ho 2: Falcon Vision

**Date:** [today's date]  
**Duration:** [actual time spent]  
**Status:** Complete ✓

## What Was Built

### Detection System
- [x] Core detection function (`detect.py`)
- [x] Sample video download script
- [x] Detection test script (`run_detection.py`)
- [x] Visualization script (optional)
- [x] Tests for detection logic

### Key Functions Created
- `detect_falcons()` - Main detection function
- `save_detections()` - Save results to JSON
- `visualize_detections()` - Create annotated images

## Understanding Check

**Can I explain:**
- [x] How YOLOv8 detection works (at high level)
- [x] What confidence threshold means
- [x] Why we process every Nth frame (performance)
- [x] What bounding boxes represent
- [x] How to modify detection parameters

**Tier Assessment:**
- **Tier 1 (Black Box):** YOLOv8 internals, OpenCV video codecs
- **Tier 2 (Functional):** Detection flow, frame processing, filtering logic ✓
- **Tier 3 (Deep):** Not needed yet

## Detection Results

**Sample video:**
- Duration: [X] seconds
- Detections: [X] frames with birds
- Confidence range: [X] to [X]
- Average birds per frame: [X]

**Performance:**
- Processing time: [X] seconds
- Frames per second: [X]

## Python Concepts Learned

### New Concepts
- Working with OpenCV VideoCapture
- YOLOv8 API and detection results
- Processing video frame-by-frame
- Bounding box coordinates
- JSON data serialization

### Syntax Used
- Type hints: `str | Path`
- List comprehensions: `[x for x in list if condition]`
- Dictionary unpacking
- Context managers: `with open()`

## Challenges Encountered

[Document any issues you faced]

## Code Quality

**Tests:**
- Detection tests: ✓ Passing
- Coverage: [X]% of detect.py

**Formatting:**
- [x] black formatting applied
- [x] isort imports organized
- [x] flake8 checks passing

## Visualizations Created

[Note: Did you create visualization images? What did they show?]

## Key Insights

### About Detection
[What surprised you about how detection works?]

### About YOLOv8
[How accurate was it? False positives/negatives?]

### About Video Processing
[Performance observations? Frame rate considerations?]

## Next Steps

**Ready for Ho 3: Event Detection**
- Implement enter/exit event logic
- Track falcon movement patterns
- Detect significant activity changes

## Files Created/Modified

**New files:**
- `src/kanyo/detection/detect.py`
- `scripts/download_sample.sh`
- `scripts/run_detection.py`
- `scripts/visualize_detections.py`
- `devlog/ho-02-falcon-vision.md`

**Modified:**
- `tests/test_detection.py` (added real tests)

**Generated:**
- `data/samples/falcon_sample.mp4` (sample video)
- `data/detections.json` (detection results)
- `data/visualizations/*.jpg` (optional visualizations)
- `models/yolov8n.pt` (downloaded model)

---

**Completed:** [timestamp]  
**Detections Found:** ___  
**Tests Passing:** ✓  
**Confidence Level (1-5):** ___
```

**Create the file:**

```bash
cd ~/vaults/dev/kanyo-dev
code devlog/ho-02-falcon-vision.md
```

**Fill in your actual results!**

---

## Part 7: Commit Your Work (5 minutes)

### Overview

Save all your Ho 2 work to git.

---

### Commit Everything

```bash
cd ~/vaults/dev/kanyo-dev

# Check status
git status

# Add all Ho 2 files
git add src/kanyo/detection/detect.py
git add scripts/download_sample.sh
git add scripts/run_detection.py
git add scripts/visualize_detections.py
git add tests/test_detection.py
git add devlog/ho-02-falcon-vision.md

# DON'T add data files (too large, in .gitignore)
# git will ignore: data/samples/, data/detections.json, models/*.pt

# Commit
git commit -m "Ho 2: Implement falcon detection with YOLOv8

- Add core detection function (detect.py)
- Create sample video download script
- Add detection test script with visualization
- Write tests for detection functionality
- Document Ho 2 in devlog"

# Push
git push
```

---

## Ho 2 Completion Checklist

**Before moving to Ho 3, verify:**

- [ ] Sample video downloaded (`data/samples/falcon_sample.mp4`)
- [ ] YOLOv8 model downloaded (`models/yolov8n.pt`)
- [ ] Detection script works (`python scripts/run_detection.py`)
- [ ] Got detection results (JSON file created)
- [ ] Tests pass (`pytest tests/test_detection.py`)
- [ ] Visualizations created (optional but recommended)
- [ ] Devlog completed with actual results
- [ ] All code committed and pushed

---

## Understanding Verification

**Can you answer these?**

**Q: What does YOLOv8 do?**

```
Detects objects in images/video
Returns bounding boxes, classes, and confidence scores
We use it to find birds (class 14)
```

**Q: Why process every 30th frame instead of every frame?**

```
Performance - processing every frame is slow
At 30fps, every 30th frame = ~1 per second
Still catches falcon presence without overloading
```

**Q: What is a confidence threshold?**

```
Minimum score (0.0-1.0) to keep a detection
0.5 = 50% confident it's a bird
Higher = fewer false positives, might miss some birds
Lower = more detections, more false positives
```

**Q: What's in the bounding box array [x, y, w, h]?**

```
x, y = center position of detected object
w, h = width and height
We convert to corner coordinates for drawing
```

**Q: Where is the detection logic (Tier 2 vs Tier 1)?**

```
Tier 1 (black box): YOLOv8 model, OpenCV internals
Tier 2 (your logic): Frame processing loop, filtering for birds, storing results
I need to understand Tier 2 to modify/debug
```

**If you can answer these: You have Tier 2 understanding! ✓**

---

## What's Next?

**Ho 3: "Event Detection"** will build on this:

- Detect when falcons enter/exit frame
- Identify movement after stillness
- Track multiple birds
- Create meaningful events from raw detections

**Ho 2 gives you:** Raw detections (bird at 10.3s, bird at 15.7s, etc.)  
**Ho 3 will give you:** Events (falcon entered at 10.3s, exited at 45.2s)

---

## Troubleshooting

### "No detections found"

**Try:**

```bash
# Lower confidence threshold
python scripts/run_detection.py --confidence 0.3

# Or visualize to see what's happening
python scripts/visualize_detections.py
```

### "Model file not found"

**YOLOv8 downloads automatically on first run.** If it fails:

```bash
# Create models directory
mkdir -p models

# Download manually
wget https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt -O models/yolov8n.pt
```

### "Video file not found"

```bash
# Download sample
./scripts/download_sample.sh

# Or specify different video
python scripts/run_detection.py --video /path/to/your/video.mp4
```

### Tests failing

```bash
# Make sure sample video exists
ls data/samples/falcon_sample.mp4

# Run with verbose output
pytest tests/test_detection.py -v -s
```

---

## Reflection Questions

**Before Ho 3, think about:**

1. How accurate was detection? False positives?
2. Is confidence=0.5 the right threshold?
3. Is processing every 30th frame enough?
4. What would you change about the detection logic?

**When ready:** Return to Claude.ai and report:

> "Ho 2 complete! Detected [X] birds in sample video. Detection working well / needs tuning. Ready for Ho 3."

---

**Completed:** ___________  
**Time Spent:** ___________  
**Detections Found:** ___________  
**Confidence Level (1-5):** ___________