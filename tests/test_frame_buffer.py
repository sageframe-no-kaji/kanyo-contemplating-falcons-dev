"""Tests for frame buffer module."""

from datetime import datetime, timedelta

import numpy as np

from kanyo.utils.frame_buffer import BufferedFrame, FrameBuffer


class TestBufferedFrame:
    """Tests for BufferedFrame dataclass."""

    def test_creation(self):
        """Test creating a buffered frame."""
        timestamp = datetime.now()
        jpeg_data = b"\xff\xd8\xff\xe0\x00\x10JFIF"  # Fake JPEG header

        frame = BufferedFrame(
            timestamp=timestamp,
            frame_number=42,
            jpeg_data=jpeg_data,
        )

        assert frame.timestamp == timestamp
        assert frame.frame_number == 42
        assert frame.jpeg_data == jpeg_data

    def test_decode_returns_ndarray(self):
        """Test that decode produces a valid numpy array."""
        # Create a small real image and encode it
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        img[:, :, 2] = 255  # Red

        import cv2

        success, jpeg = cv2.imencode(".jpg", img)
        assert success

        frame = BufferedFrame(
            timestamp=datetime.now(),
            frame_number=1,
            jpeg_data=jpeg.tobytes(),
        )

        decoded = frame.decode()
        assert isinstance(decoded, np.ndarray)
        assert decoded.shape[0] == 10
        assert decoded.shape[1] == 10
        assert decoded.shape[2] == 3


class TestFrameBufferInit:
    """Tests for FrameBuffer initialization."""

    def test_default_init(self):
        """Test default initialization."""
        buffer = FrameBuffer()
        assert buffer.buffer_seconds == 60
        assert buffer.fps == 30
        assert buffer.jpeg_quality == 85
        assert buffer.max_frames == 60 * 30

    def test_custom_init(self):
        """Test custom initialization."""
        buffer = FrameBuffer(buffer_seconds=120, fps=15, jpeg_quality=70)
        assert buffer.buffer_seconds == 120
        assert buffer.fps == 15
        assert buffer.jpeg_quality == 70
        assert buffer.max_frames == 120 * 15


class TestFrameBufferAddFrame:
    """Tests for adding frames to buffer."""

    def test_add_single_frame(self):
        """Test adding a single frame."""
        buffer = FrameBuffer(buffer_seconds=10, fps=1)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        timestamp = datetime.now()

        buffer.add_frame(frame, timestamp, frame_number=1)

        assert len(buffer._frames) == 1
        assert buffer._frames[0].frame_number == 1

    def test_add_multiple_frames(self):
        """Test adding multiple frames."""
        buffer = FrameBuffer(buffer_seconds=10, fps=1)
        base_time = datetime.now()

        for i in range(5):
            frame = np.zeros((100, 100, 3), dtype=np.uint8)
            buffer.add_frame(frame, base_time + timedelta(seconds=i), frame_number=i)

        assert len(buffer._frames) == 5

    def test_buffer_eviction(self):
        """Test that old frames are evicted when buffer is full."""
        buffer = FrameBuffer(buffer_seconds=2, fps=1)  # Only 2 frames max
        base_time = datetime.now()

        # Add 5 frames, but buffer can only hold 2
        for i in range(5):
            frame = np.zeros((50, 50, 3), dtype=np.uint8)
            buffer.add_frame(frame, base_time + timedelta(seconds=i), frame_number=i)

        # Only last 2 frames should remain
        assert len(buffer._frames) == 2
        assert buffer._frames[0].frame_number == 3
        assert buffer._frames[1].frame_number == 4


class TestFrameBufferGetFrames:
    """Tests for retrieving frames from buffer."""

    def _fill_buffer(self, buffer: FrameBuffer, count: int, start_time: datetime):
        """Helper to fill buffer with frames."""
        for i in range(count):
            frame = np.zeros((50, 50, 3), dtype=np.uint8)
            buffer.add_frame(frame, start_time + timedelta(seconds=i), frame_number=i)

    def test_get_frames_in_range(self):
        """Test getting frames within a time range."""
        buffer = FrameBuffer(buffer_seconds=30, fps=1)
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        self._fill_buffer(buffer, 20, base_time)

        # Get frames from seconds 5-10
        start = base_time + timedelta(seconds=5)
        end = base_time + timedelta(seconds=10)
        frames = buffer.get_frames_in_range(start, end)

        assert len(frames) == 6  # 5, 6, 7, 8, 9, 10 (inclusive)
        assert frames[0].frame_number == 5
        assert frames[-1].frame_number == 10

    def test_get_frames_before(self):
        """Test getting frames before a timestamp."""
        buffer = FrameBuffer(buffer_seconds=30, fps=1)
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        self._fill_buffer(buffer, 20, base_time)

        # Get 5 seconds before second 15 (inclusive of reference)
        reference = base_time + timedelta(seconds=15)
        frames = buffer.get_frames_before(reference, seconds=5)

        # Should get frames 10-15 (inclusive range)
        assert len(frames) == 6
        assert frames[0].frame_number == 10
        assert frames[-1].frame_number == 15

    def test_get_recent_frames(self):
        """Test getting most recent frames."""
        buffer = FrameBuffer(buffer_seconds=30, fps=1)
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        self._fill_buffer(buffer, 20, base_time)

        # Get last 5 seconds
        frames = buffer.get_recent_frames(seconds=5)

        assert len(frames) == 6  # Frames 15-19 + the boundary
        assert frames[-1].frame_number == 19

    def test_get_frames_empty_range(self):
        """Test getting frames from empty range returns empty list."""
        buffer = FrameBuffer(buffer_seconds=30, fps=1)
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        self._fill_buffer(buffer, 10, base_time)

        # Request range outside buffer
        far_future = base_time + timedelta(hours=1)
        frames = buffer.get_frames_in_range(far_future, far_future + timedelta(seconds=5))

        assert frames == []


class TestFrameBufferClear:
    """Tests for clearing the buffer."""

    def test_clear_buffer(self):
        """Test clearing all frames."""
        buffer = FrameBuffer(buffer_seconds=30, fps=1)
        base_time = datetime.now()

        # Add some frames
        for i in range(10):
            frame = np.zeros((50, 50, 3), dtype=np.uint8)
            buffer.add_frame(frame, base_time + timedelta(seconds=i), frame_number=i)

        assert len(buffer._frames) == 10

        buffer.clear()

        assert len(buffer._frames) == 0


class TestFrameBufferProperties:
    """Tests for buffer properties."""

    def test_oldest_newest_timestamps(self):
        """Test getting oldest and newest timestamps."""
        buffer = FrameBuffer(buffer_seconds=30, fps=1)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        # Empty buffer
        assert buffer.oldest_timestamp is None
        assert buffer.newest_timestamp is None

        # Add frames
        for i in range(10):
            frame = np.zeros((50, 50, 3), dtype=np.uint8)
            buffer.add_frame(frame, base_time + timedelta(seconds=i), frame_number=i)

        assert buffer.oldest_timestamp == base_time
        assert buffer.newest_timestamp == base_time + timedelta(seconds=9)

    def test_len_dunder(self):
        """Test __len__ method."""
        buffer = FrameBuffer(buffer_seconds=30, fps=1)

        assert len(buffer) == 0

        frame = np.zeros((50, 50, 3), dtype=np.uint8)
        buffer.add_frame(frame, datetime.now(), frame_number=0)

        assert len(buffer) == 1

    def test_duration_seconds(self):
        """Test duration_seconds property."""
        buffer = FrameBuffer(buffer_seconds=30, fps=10)  # 10 fps

        assert buffer.duration_seconds == 0

        # Add 50 frames at 10fps = 5 seconds
        for i in range(50):
            frame = np.zeros((50, 50, 3), dtype=np.uint8)
            buffer.add_frame(frame, datetime.now(), frame_number=i)

        assert buffer.duration_seconds == 5.0
