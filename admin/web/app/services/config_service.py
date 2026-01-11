"""Configuration file management service."""

import yaml


def read_config(config_path: str) -> dict:
    """
    Load and return config.yaml contents.

    Args:
        config_path: Path to config.yaml file

    Returns:
        Config dictionary
    """
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def write_config(config_path: str, config: dict) -> None:
    """
    Write config dict to YAML file.

    Args:
        config_path: Path to config.yaml file
        config: Configuration dictionary
    """
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)


def validate_config(config: dict) -> list[str]:
    """
    Validate configuration.

    Args:
        config: Configuration dictionary

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    # Check required fields
    if not config.get("video_source"):
        errors.append("video_source is required")

    # Check roosting_threshold > exit_timeout
    roosting_threshold = config.get("roosting_threshold", 0)
    exit_timeout = config.get("exit_timeout", 0)

    if roosting_threshold and exit_timeout:
        if roosting_threshold <= exit_timeout:
            errors.append("roosting_threshold must be greater than exit_timeout")

    return errors
