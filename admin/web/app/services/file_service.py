"""
File management service for stream files.

Handles operations on stream data directories including cleanup of temporary files.
"""

from pathlib import Path
from typing import Dict, List


def cleanup_temp_files(stream_id: str, data_path: str = "/data") -> Dict[str, any]:
    """
    Clean up temporary files (.tmp and .ffmpeg.log) in stream clips directory.

    Args:
        stream_id: Stream identifier
        data_path: Base data path (default: /data)

    Returns:
        Dictionary with cleanup results:
            - files_deleted: Number of files deleted
            - bytes_freed: Total bytes freed
            - deleted_files: List of deleted file names
    """
    clips_dir = Path(data_path) / stream_id / "clips"

    if not clips_dir.exists():
        return {"files_deleted": 0, "bytes_freed": 0, "deleted_files": []}

    deleted_files: List[str] = []
    bytes_freed = 0

    # Find all .tmp and .ffmpeg.log files recursively
    for pattern in ["**/*.tmp", "**/*.ffmpeg.log"]:
        for file_path in clips_dir.glob(pattern):
            if file_path.is_file():
                try:
                    file_size = file_path.stat().st_size
                    file_name = str(file_path.relative_to(clips_dir))
                    file_path.unlink()
                    deleted_files.append(file_name)
                    bytes_freed += file_size
                except Exception as e:
                    # Log error but continue with other files
                    print(f"Error deleting {file_path}: {e}")

    return {
        "files_deleted": len(deleted_files),
        "bytes_freed": bytes_freed,
        "deleted_files": deleted_files,
    }
