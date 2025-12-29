"""Docker container management service."""

from typing import Optional
import docker
from docker.errors import NotFound, APIError
from datetime import datetime, timezone


def _get_docker_client():
    """Get Docker client, return None if not available."""
    try:
        client = docker.from_env()
        # Test connection
        client.ping()
        return client
    except Exception:
        return None


def get_container_status(container_name: str) -> dict:
    """
    Get container status and uptime.

    Args:
        container_name: Name of the container

    Returns:
        Dict with status and uptime, or status='not_found'
    """
    client = _get_docker_client()
    if not client:
        return {"status": "unknown", "uptime": "Docker unavailable"}

    try:
        container = client.containers.get(container_name)
        status = container.status

        # Calculate uptime for running containers
        uptime = ""
        if status == "running":
            started_at = container.attrs["State"]["StartedAt"]
            # Parse ISO 8601 timestamp
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta = now - started

            hours = int(delta.total_seconds() // 3600)
            minutes = int((delta.total_seconds() % 3600) // 60)
            if hours > 0:
                uptime = f"{hours}h {minutes}m"
            else:
                uptime = f"{minutes}m"

        return {"status": status, "uptime": uptime}
    except NotFound:
        return {"status": "not_found"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def restart_container(container_name: str) -> bool:
    """
    Restart container.

    Args:
        container_name: Name of the container

    Returns:
        True if successful, False otherwise
    """
    client = _get_docker_client()
    if not client:
        return False

    try:
        container = client.containers.get(container_name)
        container.restart()
        return True
    except (NotFound, APIError):
        return False


def stop_container(container_name: str) -> bool:
    """
    Stop container.

    Args:
        container_name: Name of the container

    Returns:
        True if successful, False otherwise
    """
    client = _get_docker_client()
    if not client:
        return False

    try:
        container = client.containers.get(container_name)
        container.stop()
        return True
    except (NotFound, APIError):
        return False


def start_container(container_name: str) -> bool:
    """
    Start container.

    Args:
        container_name: Name of the container

    Returns:
        True if successful, False otherwise
    """
    client = _get_docker_client()
    if not client:
        return False

    try:
        container = client.containers.get(container_name)
        container.start()
        return True
    except (NotFound, APIError):
        return False


def get_logs(container_name: str, lines: int = 100) -> str:
    """
    Get container logs.

    Args:
        container_name: Name of the container
        lines: Number of recent log lines to retrieve

    Returns:
        Log lines as string
    """
    client = _get_docker_client()
    if not client:
        return "Docker unavailable"

    try:
        container = client.containers.get(container_name)
        logs = container.logs(tail=lines, timestamps=True)
        return logs.decode('utf-8')
    except NotFound:
        return f"Container {container_name} not found"
    except Exception as e:
        return f"Error retrieving logs: {str(e)}"
