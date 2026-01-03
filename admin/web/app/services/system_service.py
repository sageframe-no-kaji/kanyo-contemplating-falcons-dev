"""System monitoring service for CPU, GPU, memory, disk, and Docker stats."""

import psutil
import shutil
from typing import Optional
from pathlib import Path


def get_system_stats() -> dict:
    """
    Collect system statistics including CPU, memory, disk, GPU, and Docker info.

    Returns:
        Dict with system stats or error info
    """
    stats = {}

    # CPU Usage
    try:
        stats["cpu_percent"] = psutil.cpu_percent(interval=1)
        stats["cpu_count"] = psutil.cpu_count()
    except Exception as e:
        stats["cpu_error"] = str(e)

    # Memory Usage
    try:
        mem = psutil.virtual_memory()
        stats["memory_total_gb"] = round(mem.total / (1024**3), 1)
        stats["memory_used_gb"] = round(mem.used / (1024**3), 1)
        stats["memory_percent"] = mem.percent
    except Exception as e:
        stats["memory_error"] = str(e)

    # Disk Usage (for /opt/services or /)
    try:
        # Try to get disk usage for /opt/services, fall back to /
        disk_path = Path("/opt/services")
        if not disk_path.exists():
            disk_path = Path("/")

        disk = shutil.disk_usage(disk_path)
        stats["disk_total_gb"] = round(disk.total / (1024**3), 1)
        stats["disk_used_gb"] = round(disk.used / (1024**3), 1)
        stats["disk_free_gb"] = round(disk.free / (1024**3), 1)
        stats["disk_percent"] = round((disk.used / disk.total) * 100, 1)
        stats["disk_path"] = str(disk_path)
    except Exception as e:
        stats["disk_error"] = str(e)

    # GPU Info (try nvidia-smi)
    try:
        import subprocess

        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            gpu_info = result.stdout.strip().split(",")
            if len(gpu_info) >= 3:
                stats["gpu_name"] = gpu_info[0].strip()
                stats["gpu_memory_used_mb"] = int(float(gpu_info[1].strip()))
                stats["gpu_memory_total_mb"] = int(float(gpu_info[2].strip()))
                stats["gpu_memory_percent"] = round(
                    (stats["gpu_memory_used_mb"] / stats["gpu_memory_total_mb"]) * 100, 1
                )
        else:
            stats["gpu_available"] = False
    except FileNotFoundError:
        stats["gpu_available"] = False
    except Exception as e:
        stats["gpu_error"] = str(e)

    # Docker Container Count
    try:
        import docker

        client = docker.from_env()
        containers = client.containers.list()
        stats["docker_containers_running"] = len(containers)
        stats["docker_containers_total"] = len(client.containers.list(all=True))
    except Exception as e:
        stats["docker_error"] = str(e)

    return stats


def get_stream_docker_stats(container_name: str) -> Optional[dict]:
    """
    Get Docker stats for a specific container.

    Args:
        container_name: Name of the container

    Returns:
        Dict with CPU, memory stats for the container, or None if unavailable
    """
    try:
        import docker

        client = docker.from_env()
        container = client.containers.get(container_name)

        # Get stats (one-shot, no streaming)
        stats = container.stats(stream=False)

        # Calculate CPU percentage
        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
        )
        cpu_percent = 0.0
        if system_delta > 0:
            cpu_percent = (
                (cpu_delta / system_delta)
                * len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1]))
                * 100
            )

        # Calculate memory
        memory_usage = stats["memory_stats"].get("usage", 0)
        memory_limit = stats["memory_stats"].get("limit", 1)
        memory_percent = (memory_usage / memory_limit) * 100 if memory_limit > 0 else 0

        return {
            "cpu_percent": round(cpu_percent, 1),
            "memory_mb": round(memory_usage / (1024**2), 1),
            "memory_limit_mb": round(memory_limit / (1024**2), 1),
            "memory_percent": round(memory_percent, 1),
        }
    except Exception:
        return None
