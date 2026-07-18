"""Tests for the compose snippet generator — the bounded issue #7 slice.

The generator emits paste-ready text (compose service block + .env addition
+ start command) and nothing else: no orchestration. These tests pin the
generated block against the canonical template's per-stream pattern by
parsing docker/docker-compose.yml and comparing against the bigbear-gpu
profile example, so template drift fails the suite.

compose_snippet has no fastapi/PIL dependency, so it is imported directly
(same pattern as the other admin service tests).
"""

import sys
from pathlib import Path

import yaml

# Import from admin web app
sys.path.insert(0, str(Path(__file__).parent.parent / "admin" / "web"))

from app.services import compose_snippet  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
COMPOSE_TEMPLATE = REPO_ROOT / "docker" / "docker-compose.yml"


def _parse_service_block(stream_id: str) -> dict:
    """Parse the generated block as YAML, satisfying the anchor reference."""
    doc = (
        "x-kanyo-gpu-service: &kanyo-gpu-service\n"
        "  image: test\n"
        "services:\n" + compose_snippet.build_service_block(stream_id)
    )
    return yaml.safe_load(doc)["services"][f"{stream_id}-gpu"]


class TestEnvVarName:
    def test_simple_id(self):
        assert compose_snippet.env_var_name("humspot") == "KANYO_HUMSPOT_ROOT"

    def test_hyphens_become_underscores(self):
        assert compose_snippet.env_var_name("cornell-redtail") == "KANYO_CORNELL_REDTAIL_ROOT"


class TestServiceBlock:
    def test_block_parses_as_yaml(self):
        service = _parse_service_block("humspot")
        assert service["container_name"] == "kanyo-humspot-gpu"

    def test_inherits_gpu_anchor(self):
        """The block must reuse the template's x-kanyo-gpu-service anchor."""
        assert "<<: *kanyo-gpu-service" in compose_snippet.build_service_block("humspot")

    def test_profile_is_opt_in(self):
        """Pasting the block must not change what plain `up -d` starts —
        the stream gets a profile like the template's bigbear example."""
        service = _parse_service_block("humspot")
        assert service["profiles"] == ["humspot"]

    def test_volumes_mirror_template_pattern(self):
        """# MIRROR pin: generated volumes must match the canonical template's
        per-stream pattern (bigbear-gpu), modulo the env var name."""
        template = yaml.safe_load(COMPOSE_TEMPLATE.read_text())
        bigbear_volumes = template["services"]["bigbear-gpu"]["volumes"]
        expected = [v.replace("KANYO_CAM6_ROOT", "KANYO_HUMSPOT_ROOT") for v in bigbear_volumes]

        service = _parse_service_block("humspot")
        assert service["volumes"] == expected

    def test_hyphenated_stream_id(self):
        service = _parse_service_block("cornell-redtail")
        assert service["container_name"] == "kanyo-cornell-redtail-gpu"
        assert "${KANYO_CORNELL_REDTAIL_ROOT}/config.yaml:/app/config.yaml:ro" in (
            service["volumes"]
        )


class TestEnvLines:
    def test_env_line_points_at_host_stream_dir(self):
        assert (
            compose_snippet.build_env_lines("humspot")
            == "KANYO_HUMSPOT_ROOT=/opt/services/kanyo-humspot\n"
        )

    def test_custom_host_root(self):
        assert (
            compose_snippet.build_env_lines("humspot", host_root="/srv/kanyo")
            == "KANYO_HUMSPOT_ROOT=/srv/kanyo/kanyo-humspot\n"
        )


class TestFullSnippet:
    def test_snippet_carries_all_three_parts(self):
        snippet = compose_snippet.build_snippet("humspot")
        assert "KANYO_HUMSPOT_ROOT=/opt/services/kanyo-humspot" in snippet
        assert "humspot-gpu:" in snippet
        assert "docker compose --profile humspot up -d humspot-gpu" in snippet

    def test_snippet_mentions_paste_targets(self):
        snippet = compose_snippet.build_snippet("humspot")
        assert "/opt/services/kanyo-admin/.env" in snippet
        assert "/opt/services/kanyo-admin/docker-compose.yml" in snippet

    def test_up_command(self):
        assert (
            compose_snippet.build_up_command("humspot")
            == "docker compose --profile humspot up -d humspot-gpu"
        )
