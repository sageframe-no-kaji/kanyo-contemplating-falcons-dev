"""Tests for frame buffer module."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from kanyo.utils.frame_buffer import BufferedFrame, FrameBuffer


def _make_real_frame(
    timestamp: datetime | None = None,
    frame_number: int = 0,
    size: tuple[int, int] = (8, 8),
) -> BufferedFrame:
    """Build a BufferedFrame around a real tiny JPEG."""
    img = np.zeros((size[0], size[1], 3), dtype=np.uint8)
    success, jpeg = cv2.imencode(".jpg", img)
    assert success
    return BufferedFrame(
        timestamp=timestamp or datetime.now(),
        frame_number=frame_number,
        jpeg_data=jpeg.tobytes(),
    )


def _make_ffmpeg_process(returncode: int = 0, stderr: bytes = b"") -> MagicMock:
    """Mock subprocess.Popen result mimicking a piped ffmpeg process."""
    process = MagicMock()
    process.stdin = MagicMock()
    process.communicate.return_value = (b"", stderr)
    process.returncode = returncode
    return process


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


class TestFrameBufferAddFrameFailure:
    """Tests for JPEG encode failure handling."""

    def test_add_frame_encode_failure_drops_frame(self):
        """A frame that fails JPEG encoding is dropped, not buffered."""
        buffer = FrameBuffer(buffer_seconds=10, fps=1)
        frame = np.zeros((50, 50, 3), dtype=np.uint8)

        with patch("cv2.imencode", return_value=(False, None)):
            buffer.add_frame(frame, datetime.now(), frame_number=7)

        assert len(buffer) == 0


class TestFrameBufferGetRecentFramesEmpty:
    """Tests for get_recent_frames on an empty buffer."""

    def test_get_recent_frames_empty_buffer(self):
        """Empty buffer yields an empty list without error."""
        buffer = FrameBuffer(buffer_seconds=10, fps=1)
        assert buffer.get_recent_frames(seconds=5) == []


class TestFrameBufferExtractClip:
    """Tests for extract_clip (mocked ffmpeg subprocess)."""

    def _fill_buffer(self, buffer: FrameBuffer, count: int, start_time: datetime) -> None:
        for i in range(count):
            frame = np.zeros((8, 8, 3), dtype=np.uint8)
            buffer.add_frame(frame, start_time + timedelta(seconds=i), frame_number=i)

    def test_extract_clip_no_frames_in_range(self, tmp_path):
        """Extraction fails cleanly when the range contains no frames."""
        buffer = FrameBuffer(buffer_seconds=10, fps=1)
        base_time = datetime(2024, 1, 1, 12, 0, 0)

        result = buffer.extract_clip(
            base_time,
            base_time + timedelta(seconds=5),
            tmp_path / "clip.mp4",
        )

        assert result is False

    @patch("kanyo.utils.frame_buffer.detect_hardware_encoder", return_value="libx264")
    @patch("subprocess.Popen")
    def test_extract_clip_success(self, mock_popen, mock_encoder, tmp_path):
        """Frames in range are piped to ffmpeg and success is reported."""
        mock_process = _make_ffmpeg_process(returncode=0)
        mock_popen.return_value = mock_process

        buffer = FrameBuffer(buffer_seconds=30, fps=1)
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        self._fill_buffer(buffer, 10, base_time)

        output_path = tmp_path / "out" / "clip.mp4"
        result = buffer.extract_clip(
            base_time,
            base_time + timedelta(seconds=4),
            output_path,
        )

        assert result is True
        # One raw frame write per buffered frame in range (5 frames: 0-4)
        assert mock_process.stdin.write.call_count == 5
        mock_process.stdin.close.assert_called_once()
        # Output directory was created for ffmpeg
        assert output_path.parent.exists()
        # Buffer fps used when fps arg is None
        cmd = mock_popen.call_args[0][0]
        assert "-r" in cmd
        assert cmd[cmd.index("-r") + 1] == "1"

    @patch("kanyo.utils.frame_buffer.detect_hardware_encoder", return_value="libx264")
    @patch("subprocess.Popen")
    def test_extract_clip_explicit_fps_overrides_buffer_fps(
        self, mock_popen, mock_encoder, tmp_path
    ):
        """An explicit fps argument overrides the buffer's fps."""
        mock_popen.return_value = _make_ffmpeg_process(returncode=0)

        buffer = FrameBuffer(buffer_seconds=30, fps=1)
        base_time = datetime(2024, 1, 1, 12, 0, 0)
        self._fill_buffer(buffer, 5, base_time)

        result = buffer.extract_clip(
            base_time,
            base_time + timedelta(seconds=4),
            tmp_path / "clip.mp4",
            fps=24,
        )

        assert result is True
        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("-r") + 1] == "24"


class TestWriteFramesToVideo:
    """Tests for _write_frames_to_video internals (mocked ffmpeg subprocess)."""

    def test_empty_frames_returns_false(self, tmp_path):
        """No frames means nothing to write."""
        buffer = FrameBuffer(buffer_seconds=10, fps=1)
        result = buffer._write_frames_to_video([], tmp_path / "clip.mp4", fps=30, crf=23)
        assert result is False

    def test_undecodable_first_frame_returns_false(self, tmp_path):
        """Corrupt first frame aborts before ffmpeg is launched."""
        buffer = FrameBuffer(buffer_seconds=10, fps=1)
        bad_frame = BufferedFrame(
            timestamp=datetime.now(),
            frame_number=0,
            jpeg_data=b"not a jpeg",
        )

        with patch("subprocess.Popen") as mock_popen:
            result = buffer._write_frames_to_video(
                [bad_frame], tmp_path / "clip.mp4", fps=30, crf=23
            )

        assert result is False
        mock_popen.assert_not_called()

    @patch("subprocess.Popen")
    def test_videotoolbox_encoder_options(self, mock_popen, tmp_path):
        """VideoToolbox encoder maps crf to a -q:v quality value."""
        mock_popen.return_value = _make_ffmpeg_process(returncode=0)
        buffer = FrameBuffer(buffer_seconds=10, fps=1)

        with patch(
            "kanyo.utils.frame_buffer.detect_hardware_encoder",
            return_value="h264_videotoolbox",
        ):
            result = buffer._write_frames_to_video(
                [_make_real_frame()], tmp_path / "clip.mp4", fps=30, crf=23
            )

        assert result is True
        cmd = mock_popen.call_args[0][0]
        assert "h264_videotoolbox" in cmd
        assert "-q:v" in cmd
        assert cmd[cmd.index("-q:v") + 1] == str((51 - 23) * 2)

    @patch("subprocess.Popen")
    def test_vaapi_encoder_options(self, mock_popen, tmp_path):
        """VAAPI encoder adds the render device and hwupload filter."""
        mock_popen.return_value = _make_ffmpeg_process(returncode=0)
        buffer = FrameBuffer(buffer_seconds=10, fps=1)

        with patch(
            "kanyo.utils.frame_buffer.detect_hardware_encoder",
            return_value="h264_vaapi",
        ):
            result = buffer._write_frames_to_video(
                [_make_real_frame()], tmp_path / "clip.mp4", fps=30, crf=23
            )

        assert result is True
        cmd = mock_popen.call_args[0][0]
        assert "h264_vaapi" in cmd
        assert "-vaapi_device" in cmd
        assert "format=nv12,hwupload" in cmd

    @patch("subprocess.Popen")
    def test_nvenc_encoder_options(self, mock_popen, tmp_path):
        """NVENC encoder uses -cq quality control."""
        mock_popen.return_value = _make_ffmpeg_process(returncode=0)
        buffer = FrameBuffer(buffer_seconds=10, fps=1)

        with patch(
            "kanyo.utils.frame_buffer.detect_hardware_encoder",
            return_value="h264_nvenc",
        ):
            result = buffer._write_frames_to_video(
                [_make_real_frame()], tmp_path / "clip.mp4", fps=30, crf=23
            )

        assert result is True
        cmd = mock_popen.call_args[0][0]
        assert "h264_nvenc" in cmd
        assert "-cq" in cmd

    @patch("subprocess.Popen")
    def test_libx264_encoder_options(self, mock_popen, tmp_path):
        """Software fallback uses libx264 with -crf."""
        mock_popen.return_value = _make_ffmpeg_process(returncode=0)
        buffer = FrameBuffer(buffer_seconds=10, fps=1)

        with patch(
            "kanyo.utils.frame_buffer.detect_hardware_encoder",
            return_value="libx264",
        ):
            result = buffer._write_frames_to_video(
                [_make_real_frame()], tmp_path / "clip.mp4", fps=30, crf=18
            )

        assert result is True
        cmd = mock_popen.call_args[0][0]
        assert "libx264" in cmd
        assert "-crf" in cmd
        assert cmd[cmd.index("-crf") + 1] == "18"

    @patch("kanyo.utils.frame_buffer.detect_hardware_encoder", return_value="libx264")
    @patch("subprocess.Popen")
    def test_ffmpeg_nonzero_exit_returns_false(self, mock_popen, mock_encoder, tmp_path):
        """A non-zero ffmpeg exit code is reported as failure."""
        mock_popen.return_value = _make_ffmpeg_process(returncode=1, stderr=b"encode error")
        buffer = FrameBuffer(buffer_seconds=10, fps=1)

        result = buffer._write_frames_to_video(
            [_make_real_frame()], tmp_path / "clip.mp4", fps=30, crf=23
        )

        assert result is False

    @patch("kanyo.utils.frame_buffer.detect_hardware_encoder", return_value="libx264")
    @patch("subprocess.Popen")
    def test_popen_exception_returns_false(self, mock_popen, mock_encoder, tmp_path):
        """An OS-level failure launching ffmpeg is caught and reported."""
        mock_popen.side_effect = OSError("ffmpeg not found")
        buffer = FrameBuffer(buffer_seconds=10, fps=1)

        result = buffer._write_frames_to_video(
            [_make_real_frame()], tmp_path / "clip.mp4", fps=30, crf=23
        )

        assert result is False
