"""Tests for encoder module."""

from unittest.mock import MagicMock, patch

from kanyo.utils.encoder import detect_hardware_encoder


class TestEncoderImports:
    """Verify encoder module imports work."""

    def test_imports(self):
        """Verify encoder module imports."""
        from kanyo.utils import encoder

        assert encoder is not None
        assert hasattr(encoder, "detect_hardware_encoder")

    def test_import_from_clips(self):
        """Verify clips can import encoder."""
        from kanyo.generation.clips import detect_hardware_encoder as imported

        assert imported is not None


class TestDetectHardwareEncoder:
    """Tests for detect_hardware_encoder function."""

    def test_returns_string(self):
        """Encoder detection returns a string."""
        # Clear cache for fresh detection
        import kanyo.utils.encoder as encoder_module

        encoder_module._detected_encoder = None

        result = detect_hardware_encoder()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_caches_result(self):
        """Encoder detection caches result for performance."""
        import kanyo.utils.encoder as encoder_module

        # Set cache
        encoder_module._detected_encoder = "test_encoder"

        result = detect_hardware_encoder()
        assert result == "test_encoder"

        # Clear cache for other tests
        encoder_module._detected_encoder = None

    def test_verbose_bypasses_cache(self):
        """Verbose mode bypasses cache for fresh detection."""
        import kanyo.utils.encoder as encoder_module

        # Set cache
        encoder_module._detected_encoder = "cached_encoder"

        # Verbose should not return cached value
        result = detect_hardware_encoder(verbose=True)
        # Result should be a real encoder, not the fake cached one
        assert result != "cached_encoder" or result in [
            "h264_videotoolbox",
            "h264_nvenc",
            "h264_vaapi",
            "h264_qsv",
            "h264_amf",
            "libx264",
        ]

        # Clear cache
        encoder_module._detected_encoder = None

    @patch("subprocess.run")
    def test_fallback_to_libx264(self, mock_run):
        """Falls back to libx264 when no hardware encoder available."""
        import kanyo.utils.encoder as encoder_module

        encoder_module._detected_encoder = None

        # Mock: ffmpeg returns no hardware encoders
        mock_result = MagicMock()
        mock_result.stdout = "libx264"  # No hardware encoders in output
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        result = detect_hardware_encoder()
        assert result == "libx264"

        encoder_module._detected_encoder = None

    @patch("subprocess.run")
    def test_ffmpeg_not_found(self, mock_run):
        """Handles ffmpeg not being installed."""
        import kanyo.utils.encoder as encoder_module

        encoder_module._detected_encoder = None

        mock_run.side_effect = FileNotFoundError("ffmpeg not found")

        result = detect_hardware_encoder()
        assert result == "libx264"

        encoder_module._detected_encoder = None

    def test_valid_encoder_values(self):
        """Detected encoder is one of known valid values."""
        import kanyo.utils.encoder as encoder_module

        encoder_module._detected_encoder = None

        result = detect_hardware_encoder()

        valid_encoders = [
            "h264_videotoolbox",
            "h264_nvenc",
            "h264_vaapi",
            "h264_qsv",
            "h264_amf",
            "libx264",
        ]
        assert result in valid_encoders

        encoder_module._detected_encoder = None
