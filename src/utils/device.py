"""Device utilities for fingerprinting and video enumeration."""

import os
import platform
import socket
import hashlib
import uuid
import subprocess
import json
import glob
import cv2
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

def get_device_fingerprint(
    org_id: str, 
    ent_id: str, 
    selected_device: Optional[Dict] = None,
    model_config: Optional[Dict] = None
) -> Dict[str, Any]:
    """Generate comprehensive device fingerprint with metadata."""
    fingerprint_data = {}

    # Constellation identifiers
    fingerprint_data['organization_id'] = org_id
    fingerprint_data['entity_id'] = ent_id

    # Basic system information
    fingerprint_data['hostname'] = socket.gethostname()
    fingerprint_data['platform'] = {
        'system': platform.system(),
        'release': platform.release(),
        'version': platform.version(),
        'machine': platform.machine(),
        'processor': platform.processor(),
        'python_version': platform.python_version()
    }

    # Network information
    try:
        fingerprint_data['ip_address'] = socket.gethostbyname(socket.gethostname())
        fingerprint_data['fqdn'] = socket.getfqdn()
    except:
        fingerprint_data['ip_address'] = 'unknown'
        fingerprint_data['fqdn'] = 'unknown'

    # MAC address as unique identifier
    try:
        mac = uuid.getnode()
        fingerprint_data['mac_address'] = ':'.join([
            '{:02x}'.format((mac >> i) & 0xff) for i in range(0, 48, 8)
        ][::-1])
    except:
        fingerprint_data['mac_address'] = 'unknown'

    # Camera information
    if selected_device:
        fingerprint_data['camera'] = {
            'name': selected_device.get('name', 'Unknown Camera'),
            'index': selected_device.get('index', 0),
            'backend': selected_device.get('backend', 'opencv'),
            'resolution': selected_device.get('resolution', 'unknown'),
            'fps': selected_device.get('fps', 'unknown'),
            'is_native': selected_device.get('is_native', False)
        }
    else:
        fingerprint_data['camera'] = {'name': 'Default Camera', 'index': 0}

    # User and environment
    fingerprint_data['user'] = os.environ.get('USER', 'unknown')
    fingerprint_data['home'] = os.environ.get('HOME', 'unknown')

    # Generate unique device ID
    hardware_string = f"{fingerprint_data['hostname']}-{fingerprint_data['mac_address']}-{platform.machine()}"
    fingerprint_data['device_id'] = hashlib.sha256(hardware_string.encode()).hexdigest()[:16]

    # Timestamp
    fingerprint_data['fingerprinted_at'] = datetime.now(timezone.utc).isoformat()

    # Component metadata (from model config if provided)
    if model_config:
        fingerprint_data['component'] = model_config
    else:
        fingerprint_data['component'] = {
            'name': 'constellation-isr',
            'type': 'overwatch-detection',
            'version': '1.0.0'
        }

    return fingerprint_data

def enumerate_video_devices(verbose: bool = False) -> List[Dict[str, Any]]:
    """Enumerate available video capture devices."""
    devices = []

    # Get camera names from system_profiler on macOS
    camera_names = {}
    if platform.system() == 'Darwin':
        try:
            result = subprocess.run(
                ['system_profiler', 'SPCameraDataType', '-json'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if 'SPCameraDataType' in data:
                    for idx, cam in enumerate(data['SPCameraDataType']):
                        camera_names[idx] = cam.get('_name', f'Camera {idx}')
        except:
            pass

    # Suppress OpenCV warnings during enumeration
    if not verbose:
        cv2.setLogLevel(0)

    # Try indices 0-4
    for index in range(5):
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = int(cap.get(cv2.CAP_PROP_FPS))
            backend = cap.getBackendName()

            if platform.system() == 'Darwin':
                camera_name = camera_names.get(index, f'Camera {index}')
                is_native = ('FaceTime' in camera_name or 'Built-in' in camera_name or index == 0)
            else:
                camera_name = f'Camera {index}'
                is_native = False

            devices.append({
                'index': index,
                'type': 'local_camera',
                'resolution': f"{width}x{height}",
                'fps': fps,
                'backend': backend,
                'is_native': is_native,
                'name': camera_name
            })
            cap.release()

    # On Linux, check /dev/video* devices
    if platform.system() == 'Linux':
        video_devs = glob.glob('/dev/video*')
        for dev in video_devs:
            try:
                cap = cv2.VideoCapture(dev)
                if cap.isOpened():
                    devices.append({
                        'path': dev,
                        'type': 'v4l2_device',
                        'backend': 'V4L2',
                        'is_native': False,
                        'name': dev
                    })
                    cap.release()
            except:
                pass

    # Restore OpenCV logging
    if not verbose:
        cv2.setLogLevel(3)

    return devices

def print_device_list(devices: List[Dict[str, Any]]) -> None:
    """Print formatted device list."""
    print("\n=== Available Video Devices ===")
    if not devices:
        print("No video devices found.")
    else:
        for i, dev in enumerate(devices, 1):
            print(f"\n{i}. {dev.get('type', 'unknown').upper()}")
            for key, value in dev.items():
                if key != 'type':
                    print(f"   {key}: {value}")
    print()