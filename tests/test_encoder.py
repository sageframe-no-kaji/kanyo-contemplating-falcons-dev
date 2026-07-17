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


class TestDetectHardwareEncoderBranches:
    """Tests for encoder-specific detection branches (fully mocked subprocess)."""

    @patch("subprocess.run")
    def test_vaapi_uses_vaapi_device_test_command(self, mock_run, monkeypatch):
        """VAAPI detection builds a test command with -vaapi_device."""
        import kanyo.utils.encoder as encoder_module

        monkeypatch.setattr(encoder_module, "_detected_encoder", None)

        test_commands = []

        def fake_run(cmd, **kwargs):
            if "-encoders" in cmd:
                # Only VAAPI is listed as available in ffmpeg
                return MagicMock(stdout="h264_vaapi", returncode=0)
            test_commands.append(cmd)
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run

        result = detect_hardware_encoder()

        assert result == "h264_vaapi"
        assert len(test_commands) == 1
        assert "-vaapi_device" in test_commands[0]
        assert "/dev/dri/renderD128" in test_commands[0]
        assert "format=nv12,hwupload" in test_commands[0]

    @patch("subprocess.run")
    def test_test_encode_failure_falls_back_to_libx264(self, mock_run, monkeypatch, capsys):
        """Encoder listed by ffmpeg but failing its test encode is skipped."""
        import kanyo.utils.encoder as encoder_module

        monkeypatch.setattr(encoder_module, "_detected_encoder", None)

        def fake_run(cmd, **kwargs):
            if "-encoders" in cmd:
                return MagicMock(stdout="h264_nvenc", returncode=0)
            return MagicMock(returncode=1)  # Test encode fails

        mock_run.side_effect = fake_run

        result = detect_hardware_encoder(verbose=True)

        assert result == "libx264"
        captured = capsys.readouterr()
        assert "available but test failed" in captured.out

    @patch("subprocess.run")
    def test_timeout_during_test_encode_falls_back(self, mock_run, monkeypatch, capsys):
        """A test encode that hangs (TimeoutExpired) is treated as unavailable."""
        import subprocess

        import kanyo.utils.encoder as encoder_module

        monkeypatch.setattr(encoder_module, "_detected_encoder", None)

        def fake_run(cmd, **kwargs):
            if "-encoders" in cmd:
                return MagicMock(stdout="h264_videotoolbox", returncode=0)
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10)

        mock_run.side_effect = fake_run

        result = detect_hardware_encoder(verbose=True)

        assert result == "libx264"
        captured = capsys.readouterr()
        assert "timeout during test" in captured.out

    @patch("subprocess.run")
    def test_ffmpeg_not_found_verbose_reports_and_falls_back(self, mock_run, monkeypatch, capsys):
        """Missing ffmpeg binary is reported in verbose mode and detection stops."""
        import kanyo.utils.encoder as encoder_module

        monkeypatch.setattr(encoder_module, "_detected_encoder", None)

        mock_run.side_effect = FileNotFoundError("ffmpeg not found")

        result = detect_hardware_encoder(verbose=True)

        assert result == "libx264"
        captured = capsys.readouterr()
        assert "ffmpeg not found" in captured.out
        # The loop breaks on the first FileNotFoundError instead of retrying
        assert mock_run.call_count == 1
