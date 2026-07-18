"""Tests for admin stream auto-discovery (issue #5).

The dashboard discovers streams from subdirectories of DATA_PATH containing a
config.yaml — either the parent-mount layout (/data/kanyo-<id>/, preferred)
or the legacy per-stream-mount layout (/data/<id>/). Reserved service dirs
under a parent mount (kanyo-admin, kanyo-code, ...) are never streams and
never resolvable, and broken configs are logged and skipped.

stream_service has no fastapi/PIL dependency, so it is imported directly
(same pattern as test_file_service.py / test_log_service.py).
"""

import sys
from pathlib import Path

import pytest

# Import from admin web app
sys.path.insert(0, str(Path(__file__).parent.parent / "admin" / "web"))

from app.services import stream_service  # noqa: E402


def _write_stream(root: Path, dirname: str, stream_name: str = None, order: int = None) -> Path:
    """Create a stream dir with a minimal config.yaml."""
    stream_dir = root / dirname
    stream_dir.mkdir()
    lines = [
        f'stream_name: "{stream_name or dirname}"',
        'video_source: "https://youtube.com/watch?v=abc"',
        'timezone: "America/New_York"',
    ]
    if order is not None:
        lines.append(f"display:\n  order: {order}")
    (stream_dir / "config.yaml").write_text("\n".join(lines) + "\n")
    return stream_dir


@pytest.fixture
def data_path(tmp_path, monkeypatch):
    """Point the admin settings at a temp DATA_PATH."""
    monkeypatch.setattr(stream_service.settings, "DATA_PATH", tmp_path)
    return tmp_path


class TestParentMountDiscovery:
    def test_kanyo_prefixed_dirs_become_streams(self, data_path):
        _write_stream(data_path, "kanyo-harvard", "Harvard Falcons")
        _write_stream(data_path, "kanyo-nsw", "NSW Falcons")

        streams = stream_service.discover_streams()
        ids = {s["id"] for s in streams}
        assert ids == {"harvard", "nsw"}

    def test_prefix_stripped_id_but_full_dir_paths(self, data_path):
        stream_dir = _write_stream(data_path, "kanyo-harvard", "Harvard Falcons")

        (stream,) = stream_service.discover_streams()
        assert stream["id"] == "harvard"
        assert stream["name"] == "Harvard Falcons"
        assert stream["container_name"] == "kanyo-harvard-gpu"
        assert stream["data_path"] == str(stream_dir)
        assert stream["config_path"] == str(stream_dir / "config.yaml")
        assert stream["clips_path"] == str(stream_dir / "clips")
        assert stream["timezone"] == "America/New_York"

    def test_reserved_service_dirs_excluded(self, data_path):
        _write_stream(data_path, "kanyo-harvard")
        # Reserved dirs are excluded even when they carry a config.yaml
        # (e.g. a dev checkout inside kanyo-code).
        for name in ("kanyo-admin", "kanyo-code", "kanyo-nvidia", "kanyo-viewer"):
            _write_stream(data_path, name)

        streams = stream_service.discover_streams()
        assert [s["id"] for s in streams] == ["harvard"]

    def test_dirs_without_config_skipped(self, data_path):
        _write_stream(data_path, "kanyo-harvard")
        (data_path / "kanyo-empty").mkdir()
        (data_path / "not-a-dir.txt").write_text("file, not a dir")

        streams = stream_service.discover_streams()
        assert [s["id"] for s in streams] == ["harvard"]

    def test_malformed_config_logged_and_skipped(self, data_path, capsys):
        _write_stream(data_path, "kanyo-harvard")
        broken = data_path / "kanyo-broken"
        broken.mkdir()
        (broken / "config.yaml").write_text("stream_name: [unclosed\n  - ][")

        streams = stream_service.discover_streams()
        assert [s["id"] for s in streams] == ["harvard"]
        assert "kanyo-broken" in capsys.readouterr().out

    def test_new_stream_appears_without_restart_state(self, data_path):
        """New dir on disk appears on the next discovery call — no compose
        changes, no admin restart (issue #5 acceptance)."""
        assert stream_service.discover_streams() == []
        _write_stream(data_path, "kanyo-humspot")
        assert [s["id"] for s in stream_service.discover_streams()] == ["humspot"]


class TestLegacyPerStreamMounts:
    def test_unprefixed_dirs_still_discovered(self, data_path):
        """Existing per-stream mounts (/data/harvard) keep working."""
        _write_stream(data_path, "harvard", "Harvard Falcons")

        (stream,) = stream_service.discover_streams()
        assert stream["id"] == "harvard"
        assert stream["data_path"] == str(data_path / "harvard")

    def test_mixed_layouts_coexist(self, data_path):
        _write_stream(data_path, "harvard")
        _write_stream(data_path, "kanyo-nsw")

        ids = {s["id"] for s in stream_service.discover_streams()}
        assert ids == {"harvard", "nsw"}

    def test_duplicate_id_across_layouts_first_wins(self, data_path, capsys):
        _write_stream(data_path, "harvard", "Plain")
        _write_stream(data_path, "kanyo-harvard", "Prefixed")

        streams = stream_service.discover_streams()
        assert len(streams) == 1
        assert "Duplicate stream id" in capsys.readouterr().out


class TestDisplayOrderSorting:
    def test_sorted_by_display_order_then_id(self, data_path):
        _write_stream(data_path, "kanyo-zzz", order=1)
        _write_stream(data_path, "kanyo-aaa")  # no order -> 999
        _write_stream(data_path, "kanyo-bbb")  # no order -> 999

        assert [s["id"] for s in stream_service.discover_streams()] == ["zzz", "aaa", "bbb"]


class TestGetStream:
    def test_get_stream_by_stripped_id(self, data_path):
        _write_stream(data_path, "kanyo-harvard", "Harvard Falcons")
        stream = stream_service.get_stream("harvard")
        assert stream is not None
        assert stream["name"] == "Harvard Falcons"

    def test_get_stream_unknown_returns_none(self, data_path):
        assert stream_service.get_stream("nope") is None


class TestResolveStreamDir:
    def test_resolves_parent_mount_layout(self, data_path):
        stream_dir = _write_stream(data_path, "kanyo-harvard")
        assert stream_service.resolve_stream_dir("harvard") == stream_dir

    def test_resolves_legacy_layout(self, data_path):
        stream_dir = _write_stream(data_path, "harvard")
        assert stream_service.resolve_stream_dir("harvard") == stream_dir

    def test_unknown_stream_is_none(self, data_path):
        assert stream_service.resolve_stream_dir("nope") is None

    def test_dir_without_config_not_resolvable(self, data_path):
        (data_path / "kanyo-empty").mkdir()
        assert stream_service.resolve_stream_dir("empty") is None

    def test_reserved_dirs_never_resolvable(self, data_path):
        """File-serving endpoints must not expose kanyo-admin (compose, .env)
        or other service dirs through the parent mount."""
        for name in ("kanyo-admin", "kanyo-code", "kanyo-nvidia", "kanyo-viewer"):
            _write_stream(data_path, name)
        for stream_id in ("admin", "code", "nvidia", "viewer"):
            assert stream_service.resolve_stream_dir(stream_id) is None
