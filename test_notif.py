#!/usr/bin/env python3
"""Test notification with image attachment."""
import requests
from pathlib import Path

thumb_path = Path('clips/2025-12-17/falcon_222132_arrival.jpg')
url = 'https://ntfy.sh/kanyo_falcon_cam_nsw'
headers = {
    'Title': 'Python Test with Image',
    'Filename': thumb_path.name,
    'X-Message': 'Testing image attachment from Python'
}
data = thumb_path.read_bytes()
resp = requests.post(url, data=data, headers=headers, timeout=10)
print(f'Status: {resp.status_code}')
print(f'Response: {resp.text}')
