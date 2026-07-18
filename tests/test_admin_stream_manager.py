"""Tests for the admin 'Create New Stream' service (issue #6).

The form writes the on-disk stream definition only: /data/kanyo-<id>/ with a
config.yaml built from the canonical template defaults, plus clips/ and logs/.
No containers, no compose edits — bounded scope.

The admin container cannot import src/kanyo or read configs/, so
stream_manager carries mirrors of the template defaults and the validation
rules. These tests pin both mirrors against the real sources:

- TEMPLATE_DEFAULTS is compared against the parsed configs/config.template.yaml
- a created config.yaml is loaded through the real detector loader
  (kanyo.utils.config.load_config), so a form-created stream is guaranteed to
  boot the detector.

stream_manager has no fastapi/PIL dependency, so it is imported directly
(same pattern as test_file_service.py / test_admin_stream_discovery.py).
"""

import sys
from pathlib import Path

import pytest
import yaml

# Import from admin web app
sys.path.insert(0, str(Path(__file__).parent.parent / "admin" / "web"))

from app.services import stream_manager, stream_service  # noqa: E402

from kanyo.utils.config import load_config  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def data_path(tmp_path, monkeypatch):
    """Point the admin settings at a temp DATA_PATH."""
    monkeypatch.setattr(stream_manager.settings, "DATA_PATH", tmp_path)
    return tmp_path


def _minimal_config(**overrides):
    """A valid build_stream_config call with only required form fields."""
    kwargs = dict(
        name="Melbourne Falcon Cam",
        video_source="https://youtube.com/watch?v=abc123",
        timezone="Australia/Melbourne",
        short_name="Melbourne",
        location="Melbourne, Victoria, Australia",
        species="Peregrine Falcon (Falco peregrinus)",
    )
    kwargs.update(overrides)
    return stream_manager.build_stream_config(**kwargs)


class TestTemplateDefaultsMirror:
    def test_template_defaults_match_config_template_yaml(self):
        """# MIRROR pin: TEMPLATE_DEFAULTS must equal the parsed template."""
        template = yaml.safe_load((REPO_ROOT / "configs" / "config.template.yaml").read_text())
        assert stream_manager.TEMPLATE_DEFAULTS == template


class TestValidateStreamId:
    @pytest.mark.parametrize("good", ["melbourne", "cornell-redtail", "a", "big_bear2"])
    def test_valid_ids(self, good):
        valid, error = stream_manager.validate_stream_id(good)
        assert valid, error

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "Melbourne",
            "9lives",
            "-lead",
            "has space",
            "x" * 33,
            "admin",
            "code",
            "nvidia",
            "viewer",
            "new",
        ],
    )
    def test_invalid_ids(self, bad):
        valid, _ = stream_manager.validate_stream_id(bad)
        assert not valid


class TestValidateStreamForm:
    def _validate(self, **overrides):
        kwargs = dict(
            stream_id="melbourne",
            video_source="https://youtube.com/watch?v=abc123",
            timezone="Australia/Melbourne",
            detection_confidence=0.3,
            detection_confidence_ir=0.25,
            frame_interval=3,
            exit_timeout=90,
            roosting_threshold=1800,
        )
        kwargs.update(overrides)
        return stream_manager.validate_stream_form(**kwargs)

    def test_valid_form_no_errors(self):
        assert self._validate() == []

    def test_bad_stream_id_reported(self):
        assert any("Stream ID" in e for e in self._validate(stream_id="Bad Id"))

    def test_non_http_video_source_rejected(self):
        assert any("http" in e for e in self._validate(video_source="rtsp://cam"))

    def test_invalid_timezone_rejected(self):
        assert any("IANA" in e for e in self._validate(timezone="Mars/Olympus_Mons"))

    def test_confidence_out_of_range_rejected(self):
        assert self._validate(detection_confidence=1.5)
        assert self._validate(detection_confidence_ir=-0.1)

    def test_ir_confidence_optional(self):
        assert self._validate(detection_confidence_ir=None) == []

    def test_frame_interval_floor(self):
        assert any("frame_interval" in e for e in self._validate(frame_interval=0))

    def test_roosting_must_exceed_exit_timeout(self):
        """# MIRROR pin: same constraint the detector enforces at load."""
        assert any(
            "roosting_threshold" in e
            for e in self._validate(exit_timeout=1800, roosting_threshold=1800)
        )

    def test_multiple_errors_collected(self):
        errors = self._validate(stream_id="", video_source="ftp://x", timezone="nope")
        assert len(errors) >= 3


class TestBuildStreamConfig:
    def test_form_values_override_template(self):
        config = _minimal_config()
        assert config["stream_name"] == "Melbourne Falcon Cam"
        assert config["video_source"] == "https://youtube.com/watch?v=abc123"
        assert config["timezone"] == "Australia/Melbourne"

    def test_template_defaults_carried_through(self):
        config = _minimal_config()
        assert config["exit_timeout"] == 90
        assert config["roosting_threshold"] == 1800
        assert config["presence_enabled"] is True
        assert config["creature_name"] == "falcon"
        assert config["log_file"] == "logs/kanyo.log"

    def test_display_built_from_form_not_placeholders(self):
        """Template placeholder display values must never reach a real stream."""
        config = _minimal_config()
        display = config["display"]
        assert display["short_name"] == "Melbourne"
        assert display["location"] == "Melbourne, Victoria, Australia"
        assert display["species"] == "Peregrine Falcon (Falco peregrinus)"
        # Optional fields omitted, not filled with template examples
        assert "coordinates" not in display
        assert "maintainer" not in display
        assert "maintainer_url" not in display
        assert "description" not in display

    def test_optional_display_fields_included_when_given(self):
        config = _minimal_config(
            latitude=-37.8136,
            longitude=144.9631,
            maintainer="Victorian Peregrine Project",
            maintainer_url="https://example.org",
            description="Nest box on a city ledge.",
        )
        display = config["display"]
        assert display["coordinates"] == [-37.8136, 144.9631]
        assert display["maintainer"] == "Victorian Peregrine Project"
        assert display["maintainer_url"] == "https://example.org"
        assert display["description"] == "Nest box on a city ledge."

    def test_advanced_overrides_applied(self):
        config = _minimal_config(
            detection_confidence=0.4,
            detection_confidence_ir=0.2,
            frame_interval=5,
            exit_timeout=120,
            roosting_threshold=2400,
            detect_any_animal=False,
            presence_enabled=False,
            significance_filter_enabled=False,
            bird_count_enabled=True,
        )
        assert config["detection_confidence"] == 0.4
        assert config["detection_confidence_ir"] == 0.2
        assert config["frame_interval"] == 5
        assert config["exit_timeout"] == 120
        assert config["roosting_threshold"] == 2400
        assert config["detect_any_animal"] is False
        assert config["presence_enabled"] is False
        assert config["significance_filter_enabled"] is False
        assert config["bird_count_enabled"] is True

    def test_telegram_settings(self):
        config = _minimal_config(telegram_enabled=True, telegram_channel="@melbs_falcons")
        assert config["telegram_enabled"] is True
        assert config["telegram_channel"] == "@melbs_falcons"

    def test_template_defaults_not_mutated(self):
        before = stream_manager.TEMPLATE_DEFAULTS["display"].copy()
        _minimal_config(latitude=1.0, longitude=2.0)
        assert stream_manager.TEMPLATE_DEFAULTS["display"] == before


class TestCreateStream:
    def test_creates_directory_structure(self, data_path):
        ok, message = stream_manager.create_stream("melbourne", _minimal_config())
        assert ok, message
        stream_dir = data_path / "kanyo-melbourne"
        assert message == str(stream_dir)
        assert (stream_dir / "config.yaml").is_file()
        assert (stream_dir / "clips").is_dir()
        assert (stream_dir / "logs").is_dir()

    def test_written_yaml_round_trips(self, data_path):
        config = _minimal_config()
        stream_manager.create_stream("melbourne", config)
        written = yaml.safe_load((data_path / "kanyo-melbourne" / "config.yaml").read_text())
        assert written == config

    def test_created_stream_is_discoverable(self, data_path, monkeypatch):
        """Issue #6 acceptance: new stream appears in the dashboard — with
        auto-discovery (issue #5) it appears immediately, no restart."""
        monkeypatch.setattr(stream_service.settings, "DATA_PATH", data_path)
        stream_manager.create_stream("melbourne", _minimal_config())
        (stream,) = stream_service.discover_streams()
        assert stream["id"] == "melbourne"
        assert stream["name"] == "Melbourne Falcon Cam"

    def test_created_config_loads_in_detector(self, data_path):
        """# MIRROR pin: the real detector loader (validation included) must
        accept a form-created config unchanged."""
        stream_manager.create_stream("melbourne", _minimal_config())
        loaded = load_config(data_path / "kanyo-melbourne" / "config.yaml")
        assert loaded["stream_name"] == "Melbourne Falcon Cam"
        assert str(loaded["timezone_obj"]) == "Australia/Melbourne"

    def test_duplicate_prefixed_dir_rejected(self, data_path):
        stream_manager.create_stream("melbourne", _minimal_config())
        ok, message = stream_manager.create_stream("melbourne", _minimal_config())
        assert not ok
        assert "already exists" in message

    def test_duplicate_legacy_dir_rejected(self, data_path):
        (data_path / "melbourne").mkdir()
        ok, message = stream_manager.create_stream("melbourne", _minimal_config())
        assert not ok
        assert "already exists" in message

    def test_unicode_survives_yaml(self, data_path):
        """creature_emoji must round-trip as a real emoji, not an escape."""
        stream_manager.create_stream("melbourne", _minimal_config())
        text = (data_path / "kanyo-melbourne" / "config.yaml").read_text()
        assert "🦅" in text
