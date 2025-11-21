import os
from dotenv import load_dotenv

# Load .env for Constellation configuration
load_dotenv()

# YOLOE from Ultralytics with tracking capabilities
from ultralytics import YOLOE
import cv2
import asyncio
import nats
from nats.js.api import KeyValueConfig
import json
from datetime import datetime, timezone
import signal
import sys
import platform
import socket
import hashlib
import uuid
import subprocess
import argparse
import glob
import numpy as np
from collections import defaultdict

# Suppress OpenCV logging globally (must be set before any VideoCapture calls)
os.environ['OPENCV_LOG_LEVEL'] = 'SILENT'
os.environ['OPENCV_VIDEOIO_DEBUG'] = '0'
cv2.setLogLevel(0)

# Global NATS connection, JetStream context, and KV store
nc = None
js = None
kv = None
device_fingerprint = None  # Global device fingerprint
organization_id = None  # Organization identifier
entity_id = None  # Entity identifier

# KV Store name for global state
KV_STORE_NAME = "CONSTELLATION_GLOBAL_STATE"

# Root constants for subject and stream construction (still used for events)
ROOT_SUBJECT = "constellation.events.isr"
ROOT_STREAM_NAME = "CONSTELLATION_EVENTS"

# Actual subject and stream - constructed dynamically with org_id and entity_id
SUBJECT = None
STREAM_NAME = None

# Object tracking state
class TrackingState:
    """Manages state for tracked objects"""
    def __init__(self):
        self.tracked_objects = {}  # track_id -> object metadata
        self.total_unique_objects = 0
        self.total_frames_processed = 0
        self.active_track_ids = set()

    def update_object(self, track_id, label, confidence, bbox, frame_timestamp):
        """Update or create tracked object state"""
        if track_id not in self.tracked_objects:
            # New object detected
            self.total_unique_objects += 1
            self.tracked_objects[track_id] = {
                "track_id": track_id,
                "label": label,
                "first_seen": frame_timestamp,
                "last_seen": frame_timestamp,
                "frame_count": 1,
                "total_confidence": confidence,
                "avg_confidence": confidence,
                "bbox_history": [bbox],
                "is_active": True
            }
        else:
            # Update existing object
            obj = self.tracked_objects[track_id]
            obj["last_seen"] = frame_timestamp
            obj["frame_count"] += 1
            obj["total_confidence"] += confidence
            obj["avg_confidence"] = obj["total_confidence"] / obj["frame_count"]
            obj["bbox_history"].append(bbox)
            obj["is_active"] = True

            # Keep only last 30 frames of bbox history to manage memory
            if len(obj["bbox_history"]) > 30:
                obj["bbox_history"] = obj["bbox_history"][-30:]

        self.active_track_ids.add(track_id)

    def mark_inactive_objects(self, current_track_ids):
        """Mark objects that weren't seen in this frame as inactive"""
        for track_id in list(self.tracked_objects.keys()):
            if track_id not in current_track_ids:
                self.tracked_objects[track_id]["is_active"] = False
                if track_id in self.active_track_ids:
                    self.active_track_ids.remove(track_id)

    def get_persistent_objects(self, min_frames=3):
        """Get objects that have been tracked for at least min_frames (reduces noise)"""
        return {
            tid: obj for tid, obj in self.tracked_objects.items()
            if obj["frame_count"] >= min_frames
        }

    def get_analytics(self):
        """Get tracking analytics summary"""
        active_objects = [obj for obj in self.tracked_objects.values() if obj["is_active"]]

        # Count by label
        label_counts = defaultdict(int)
        for obj in active_objects:
            label_counts[obj["label"]] += 1

        return {
            "total_unique_objects": self.total_unique_objects,
            "total_frames_processed": self.total_frames_processed,
            "active_objects_count": len(active_objects),
            "tracked_objects_count": len(self.tracked_objects),
            "label_distribution": dict(label_counts),
            "active_track_ids": list(self.active_track_ids)
        }

# Global tracking state
tracking_state = TrackingState()

def get_constellation_ids():
    """Get organization_id and entity_id from environment or user input"""
    # Note: .env is loaded at module import time (top of file)

    print("\n=== Constellation Configuration ===")
    print("Initializing Constellation Overwatch Edge Awareness connection...")
    print()

    # Try to get organization_id from environment
    org_id = os.environ.get('CONSTELLATION_ORG_ID')
    if not org_id:
        print("Organization ID not found in environment (CONSTELLATION_ORG_ID)")
        print("Please obtain your Organization ID from:")
        print("  - Constellation Overwatch Edge Awareness Kit UI")
        print("  - Your Database Administrator")
        print()
        org_id = input("Enter Organization ID: ").strip()
        if not org_id:
            print("Error: Organization ID is required")
            sys.exit(1)
    else:
        print(f"Organization ID loaded from environment: {org_id}")

    # Try to get entity_id from environment
    ent_id = os.environ.get('CONSTELLATION_ENTITY_ID')
    if not ent_id:
        print("Entity ID not found in environment (CONSTELLATION_ENTITY_ID)")
        print("Please obtain your Entity ID from:")
        print("  - Constellation Overwatch Edge Awareness Kit UI")
        print("  - Your Database Administrator")
        print()
        ent_id = input("Enter Entity ID: ").strip()
        if not ent_id:
            print("Error: Entity ID is required")
            sys.exit(1)
    else:
        print(f"Entity ID loaded from environment: {ent_id}")

    print("===================================\n")

    return org_id, ent_id

def get_device_fingerprint(org_id, ent_id, selected_device=None):
    """Generate a comprehensive device fingerprint with metadata

    Args:
        org_id: Organization ID
        ent_id: Entity ID
        selected_device: Optional dict with selected camera/video device info
    """
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
        fingerprint_data['mac_address'] = ':'.join(['{:02x}'.format((mac >> i) & 0xff) for i in range(0, 48, 8)][::-1])
    except:
        fingerprint_data['mac_address'] = 'unknown'

    # Get camera information - use selected device if provided
    camera_info = get_camera_info(selected_device)
    if camera_info:
        fingerprint_data['camera'] = camera_info

    # User and environment
    fingerprint_data['user'] = os.environ.get('USER', 'unknown')
    fingerprint_data['home'] = os.environ.get('HOME', 'unknown')

    # Generate a unique device ID from hardware identifiers
    hardware_string = f"{fingerprint_data['hostname']}-{fingerprint_data['mac_address']}-{platform.machine()}"
    fingerprint_data['device_id'] = hashlib.sha256(hardware_string.encode()).hexdigest()[:16]

    # Timestamp when fingerprint was generated
    fingerprint_data['fingerprinted_at'] = datetime.now(timezone.utc).isoformat()

    # ISR component metadata
    fingerprint_data['component'] = {
        'name': 'constellation-isr',
        'type': 'yoloe-object-tracking',
        'version': '1.0.0',
        'model': 'yoloe-11l-seg',
        'tracker': 'botsort'
    }

    return fingerprint_data

def get_camera_info(selected_device=None):
    """Get camera device information

    Args:
        selected_device: Optional dict with device info from enumerate_video_devices()
                        If provided, uses this device instead of hardcoded index 0
    """
    camera_info = {}

    # If a specific device was selected, use that info
    if selected_device:
        camera_info['name'] = selected_device.get('name', 'Unknown Camera')
        camera_info['index'] = selected_device.get('index', 0)
        camera_info['backend'] = selected_device.get('backend', 'opencv')
        camera_info['resolution'] = selected_device.get('resolution', 'unknown')
        camera_info['fps'] = selected_device.get('fps', 'unknown')
        camera_info['is_native'] = selected_device.get('is_native', False)
        return camera_info

    # Fallback: try to get camera info based on platform
    if platform.system() == 'Darwin':  # macOS
        try:
            # Use system_profiler to get camera info on macOS
            result = subprocess.run(
                ['system_profiler', 'SPCameraDataType', '-json'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                if 'SPCameraDataType' in data and data['SPCameraDataType']:
                    cam = data['SPCameraDataType'][0]
                    camera_info['name'] = cam.get('_name', 'Unknown Camera')
                    camera_info['model_id'] = cam.get('model_id', 'unknown')
                    camera_info['unique_id'] = cam.get('unique_id', 'unknown')
        except:
            pass
    elif platform.system() == 'Linux':
        try:
            # Try v4l2-ctl for Linux
            result = subprocess.run(
                ['v4l2-ctl', '--list-devices'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines:
                    camera_info['name'] = lines[0].split('(')[0].strip()
        except:
            pass

    # Default camera index being used
    camera_info['index'] = 0
    camera_info['backend'] = 'opencv'

    return camera_info if camera_info else {'name': 'Default Camera', 'index': 0}

async def setup_nats(selected_device=None):
    """Connect to NATS server and create JetStream context + KV store

    Args:
        selected_device: Optional dict with selected camera/video device info
    """
    global nc, js, kv, device_fingerprint, organization_id, entity_id, SUBJECT, STREAM_NAME

    # Get constellation identifiers first
    organization_id, entity_id = get_constellation_ids()

    # Construct subject with org_id and entity_id (stream name stays constant)
    SUBJECT = f"{ROOT_SUBJECT}.{organization_id}.{entity_id}"
    STREAM_NAME = ROOT_STREAM_NAME

    print(f"Configured NATS subject: {SUBJECT}")
    print(f"Configured stream name: {STREAM_NAME}")
    print(f"Configured KV store: {KV_STORE_NAME}\n")

    nc = await nats.connect("nats://localhost:4222")
    print("Connected to NATS server")

    # Create JetStream context
    js = nc.jetstream()

    # Verify the stream exists and is configured for our subject
    try:
        stream_info = await js.stream_info(STREAM_NAME)
        print(f"Connected to JetStream stream: {STREAM_NAME}")
        print(f"Stream subjects: {stream_info.config.subjects}")
    except Exception as e:
        print(f"Warning: Stream {STREAM_NAME} not found. Messages may not be persisted.")
        print(f"Error: {e}")

    # Create or connect to KV store for global state
    try:
        kv = await js.create_key_value(config=KeyValueConfig(
            bucket=KV_STORE_NAME,
            description="Constellation global state for object tracking and analytics",
            history=10,  # Keep last 10 revisions
            ttl=3600  # 1 hour TTL for state entries
        ))
        print(f"Created/connected to KV store: {KV_STORE_NAME}")
    except Exception as e:
        # KV store might already exist
        try:
            kv = await js.key_value(KV_STORE_NAME)
            print(f"Connected to existing KV store: {KV_STORE_NAME}")
        except Exception as e2:
            print(f"Error accessing KV store: {e2}")
            print("Continuing without KV store - state will not be persisted")

    # Generate device fingerprint during bootsequence with selected device
    print("\n=== Bootsequence: Device Fingerprinting ===")
    device_fingerprint = get_device_fingerprint(organization_id, entity_id, selected_device)
    print(f"Organization ID: {device_fingerprint['organization_id']}")
    print(f"Entity ID: {device_fingerprint['entity_id']}")
    print(f"Device ID: {device_fingerprint['device_id']}")
    print(f"Hostname: {device_fingerprint['hostname']}")
    print(f"Platform: {device_fingerprint['platform']['system']} {device_fingerprint['platform']['release']}")
    print(f"Camera: {device_fingerprint['camera']['name']}")
    if selected_device:
        print(f"  Index: {device_fingerprint['camera']['index']}")
        print(f"  Resolution: {device_fingerprint['camera']['resolution']}")
        print(f"  Native: {device_fingerprint['camera']['is_native']}")
    print("=========================================\n")

    # Publish bootsequence event with device fingerprint
    bootsequence_message = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "bootsequence",
        "source": device_fingerprint,
        "message": "ISR component initialized with YOLOE tracking"
    }

    try:
        ack = await js.publish(
            SUBJECT,
            json.dumps(bootsequence_message).encode(),
            headers={
                "Content-Type": "application/json",
                "Event-Type": "bootsequence"
            }
        )
        print(f"Published bootsequence event to JetStream")
        print(f"  Stream: {ack.stream}, Seq: {ack.seq}")
    except Exception as e:
        print(f"Error publishing bootsequence: {e}")

    return nc, js, kv

async def publish_tracking_state(kv, tracking_state, entity_id):
    """Publish tracking state to NATS KV store"""
    if not kv:
        return

    try:
        # Get persistent objects (tracked for at least 3 frames)
        persistent_objects = tracking_state.get_persistent_objects(min_frames=3)

        # Get analytics
        analytics = tracking_state.get_analytics()

        # Prepare state data
        state_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entity_id": entity_id,
            "device_id": device_fingerprint['device_id'],
            "analytics": analytics,
            "tracked_objects": {
                str(tid): {
                    "track_id": obj["track_id"],
                    "label": obj["label"],
                    "first_seen": obj["first_seen"],
                    "last_seen": obj["last_seen"],
                    "frame_count": obj["frame_count"],
                    "avg_confidence": obj["avg_confidence"],
                    "is_active": obj["is_active"],
                    "current_bbox": obj["bbox_history"][-1] if obj["bbox_history"] else None
                }
                for tid, obj in persistent_objects.items()
            }
        }

        # Store in KV with hierarchical key: entity_id.detections.tracking_objects
        key = f"{entity_id}.detections.tracking_objects"
        await kv.put(key, json.dumps(state_data).encode())

        # Also store analytics separately for quick access
        analytics_key = f"{entity_id}.analytics.summary"
        await kv.put(analytics_key, json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entity_id": entity_id,
            **analytics
        }).encode())

    except Exception as e:
        print(f"Error publishing tracking state to KV: {e}")

async def cleanup():
    """Clean up NATS connection and publish shutdown event"""
    global nc, js, kv, device_fingerprint

    if js and device_fingerprint:
        # Publish shutdown event before closing
        shutdown_message = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "shutdown",
            "source": device_fingerprint,
            "message": "ISR component shutting down gracefully",
            "final_analytics": tracking_state.get_analytics()
        }

        try:
            ack = await js.publish(
                SUBJECT,
                json.dumps(shutdown_message).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Device-ID": device_fingerprint['device_id'],
                    "Event-Type": "shutdown"
                }
            )
            print(f"Published shutdown event to JetStream (Seq: {ack.seq})")
        except Exception as e:
            print(f"Error publishing shutdown event: {e}")

    if nc:
        await nc.drain()
        await nc.close()
        print("NATS connection closed")

def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    print("\nShutting down...")
    asyncio.create_task(cleanup())
    sys.exit(0)

def enumerate_video_devices(verbose=False):
    """Enumerate available video capture devices"""
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
        cv2.setLogLevel(0)  # Suppress all OpenCV logging

    # Try indices 0-4 (most systems don't have more than 4 cameras)
    # Reduce range to minimize startup time and warning messages
    for index in range(5):
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            # Get device info
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = int(cap.get(cv2.CAP_PROP_FPS))
            backend = cap.getBackendName()

            # Get actual camera name
            if platform.system() == 'Darwin':
                camera_name = camera_names.get(index, f'Camera {index}')
                # Determine if this is a built-in camera based on name
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

    # On Linux, also check /dev/video* devices
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

    # Re-enable OpenCV logging
    if not verbose:
        cv2.setLogLevel(3)  # Restore to ERROR level

    return devices

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Constellation ISR Object Tracking Client (YOLOE)')

    # Video source options
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument('--list-devices', action='store_true',
                             help='List available video devices and exit')
    source_group.add_argument('--camera', type=int, default=None,
                             help='Camera device index (e.g., 0, 1, 2)')
    source_group.add_argument('--device', type=str, default=None,
                             help='Device path (e.g., /dev/video4)')
    source_group.add_argument('--rtsp', type=str, default=None,
                             help='RTSP URL (e.g., rtsp://192.168.50.2:8554/live/stream)')
    source_group.add_argument('--http', type=str, default=None,
                             help='HTTP stream URL (e.g., http://192.168.1.100:8080/stream)')

    # Legacy RTSP options (for backward compatibility)
    parser.add_argument('--rtsp-ip', type=str, default=None,
                       help='RTSP stream IP address (legacy)')
    parser.add_argument('--rtsp-port', type=int, default=8554,
                       help='RTSP stream port (default: 8554)')
    parser.add_argument('--rtsp-path', type=str, default='/live/stream',
                       help='RTSP stream path (default: /live/stream)')

    # Additional options
    parser.add_argument('--skip-native', action='store_true',
                       help='Skip built-in/native cameras during auto-detection')

    # Tracking options
    parser.add_argument('--tracker', type=str, default='botsort.yaml',
                       choices=['botsort.yaml', 'bytetrack.yaml'],
                       help='Tracker to use (default: botsort.yaml)')
    parser.add_argument('--min-frames', type=int, default=3,
                       help='Minimum frames to track before publishing (reduces noise, default: 3)')

    return parser.parse_args()

async def main():
    # Parse command line arguments
    args = parse_args()

    # Handle --list-devices
    if args.list_devices:
        print("\n=== Available Video Devices ===")
        devices = enumerate_video_devices()
        if not devices:
            print("No video devices found.")
        else:
            for i, dev in enumerate(devices, 1):
                print(f"\n{i}. {dev.get('type', 'unknown').upper()}")
                for key, value in dev.items():
                    if key != 'type':
                        print(f"   {key}: {value}")
        print()
        return

    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)

    # =========================================================================
    # STEP 1: Determine video source BEFORE device fingerprinting
    # =========================================================================
    video_source = None
    source_type = None
    selected_device = None  # Will be populated for auto-detect mode

    if args.camera is not None:
        # Direct camera index
        video_source = args.camera
        source_type = "camera"
        print(f"\n=== Camera Mode ===")
        print(f"Using camera index: {args.camera}")
        print("===================\n")

    elif args.device:
        # Device path (e.g., /dev/video4)
        video_source = args.device
        source_type = "device"
        print(f"\n=== Device Mode ===")
        print(f"Using device: {args.device}")
        print("===================\n")

    elif args.rtsp:
        # Direct RTSP URL
        video_source = args.rtsp
        source_type = "rtsp"
        print(f"\n=== RTSP Stream Mode ===")
        print(f"Connecting to: {args.rtsp}")
        print("========================\n")

    elif args.http:
        # HTTP stream
        video_source = args.http
        source_type = "http"
        print(f"\n=== HTTP Stream Mode ===")
        print(f"Connecting to: {args.http}")
        print("========================\n")

    elif args.rtsp_ip:
        # Legacy RTSP mode (backward compatibility)
        rtsp_url = f"rtsp://{args.rtsp_ip}:{args.rtsp_port}{args.rtsp_path}"
        video_source = rtsp_url
        source_type = "rtsp"
        print(f"\n=== RTSP Stream Mode ===")
        print(f"Connecting to: {rtsp_url}")
        print("========================\n")

    else:
        # Auto-detect: try to find first available camera
        print("\n=== Auto-detecting video source ===")
        devices = enumerate_video_devices()

        # Filter out native cameras if requested
        if args.skip_native and devices:
            non_native = [d for d in devices if not d.get('is_native', False)]
            if non_native:
                devices = non_native
                print("Skipping native/built-in cameras...")
            else:
                # No non-native cameras found - ERROR OUT
                print("\nError: --skip-native specified but no external cameras found!")
                print("\nAvailable devices:")
                for dev in devices:
                    print(f"  - {dev.get('name', 'Unknown')} (native: {dev.get('is_native', False)})")
                print("\nPlease:")
                print("  1. Connect an external camera/capture device")
                print("  2. Run: uv run detect_yoloe.py --list-devices")
                print("  3. Or remove --skip-native to use built-in camera")
                sys.exit(1)

        if devices:
            selected_device = devices[0]  # Save for fingerprinting
            video_source = selected_device.get('index', selected_device.get('path', 0))
            source_type = "camera"
            print(f"Found {len(devices)} device(s)")
            print(f"Selected: {selected_device.get('name', 'Unknown')}")
            print(f"  Index: {selected_device.get('index', selected_device.get('path', 'N/A'))}")
            print(f"  Resolution: {selected_device.get('resolution', 'N/A')}")
            print(f"  FPS: {selected_device.get('fps', 'N/A')}")
            print("===================================\n")
        else:
            # No cameras found at all
            if args.skip_native:
                print("\nError: No cameras detected!")
                print("  1. Connect a camera/capture device")
                print("  2. Run: uv run detect_yoloe.py --list-devices")
                print("===================================\n")
                sys.exit(1)
            else:
                # Fallback to default camera index 0
                video_source = 0
                source_type = "camera"
                print(f"No devices detected, trying default camera (index 0)")
                print("===================================\n")

    # =========================================================================
    # STEP 2: Connect to NATS and fingerprint with selected device
    # =========================================================================
    nc, js, kv = await setup_nats(selected_device)

    # =========================================================================
    # STEP 3: Load YOLOE model
    # =========================================================================
    print("Loading YOLOE model...")

    # Get the project root directory (where this script is located)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    models_dir = os.path.join(script_dir, "models")
    model_path = os.path.join(models_dir, "yoloe-11l-seg.pt")

    # Ensure models directory exists
    os.makedirs(models_dir, exist_ok=True)

    # Check if model exists locally
    if not os.path.exists(model_path):
        print(f"Model not found at {model_path}")
        print("Downloading YOLOE-11L-SEG model...")

        # First, let Ultralytics download to its cache
        import shutil
        temp_model = YOLOE("yoloe-11l-seg.pt")

        # Find where Ultralytics cached it and copy to our models directory
        default_model_path = os.path.expanduser("~/.ultralytics/weights/yoloe-11l-seg.pt")
        if os.path.exists(default_model_path):
            shutil.copy(default_model_path, model_path)
            print(f"Model cached to: {model_path}")
        else:
            print(f"Warning: Could not find model in cache at {default_model_path}")
            # Model might be elsewhere, let's check a few common locations
            alt_paths = [
                os.path.expanduser("~/.cache/ultralytics/weights/yoloe-11l-seg.pt"),
                os.path.join(os.getcwd(), "yoloe-11l-seg.pt")
            ]
            for alt_path in alt_paths:
                if os.path.exists(alt_path):
                    shutil.copy(alt_path, model_path)
                    print(f"Model cached to: {model_path} (from {alt_path})")
                    break

    # Always load from local models directory
    if os.path.exists(model_path):
        print(f"Loading model from: {model_path}")
        model = YOLOE(model_path)
        print(f"✓ YOLOE model loaded successfully with {args.tracker} tracker")
    else:
        print(f"Error: Could not load or download model to {model_path}")
        await cleanup()
        sys.exit(1)

    # Detection confidence threshold (0.0-1.0)
    confidence_threshold = 0.25

    # Define colors for bounding boxes (COCO has 80 classes)
    # Generate more colors for all possible classes
    np.random.seed(42)
    colors = [(int(c[0]), int(c[1]), int(c[2])) for c in np.random.randint(0, 255, size=(80, 3))]

    # Open video stream with explicit backend for macOS reliability
    if source_type == "camera" and isinstance(video_source, int) and platform.system() == 'Darwin':
        # On macOS, use CAP_AVFOUNDATION explicitly to ensure correct camera selection
        cap = cv2.VideoCapture(video_source, cv2.CAP_AVFOUNDATION)
        print(f"Opening camera index {video_source} with AVFoundation backend...")
    else:
        cap = cv2.VideoCapture(video_source)

    if not cap.isOpened():
        print(f"Error: Could not open video source: {video_source}")
        print(f"Source type: {source_type}")
        if source_type == "rtsp":
            print("\nTroubleshooting RTSP:")
            print("  1. Verify the RTSP server is running")
            print("  2. Check network connectivity")
            print("  3. Confirm the RTSP URL is correct")
        elif source_type == "camera":
            print("\nTroubleshooting Camera:")
            print("  1. Check if camera is connected")
            print("  2. Run: uv run detect_yoloe.py --list-devices")
            print("  3. Try a different camera index")
        await cleanup()
        exit()

    # Verify which device was actually opened
    actual_camera_name = "Unknown"
    if source_type == "camera" and isinstance(video_source, int):
        actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = int(cap.get(cv2.CAP_PROP_FPS))
        actual_backend = cap.getBackendName()

        # Try to get actual camera name via system_profiler
        if platform.system() == 'Darwin':
            try:
                result = subprocess.run(
                    ['system_profiler', 'SPCameraDataType', '-json'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    if 'SPCameraDataType' in data and len(data['SPCameraDataType']) > video_source:
                        actual_camera_name = data['SPCameraDataType'][video_source].get('_name', 'Unknown')
            except:
                pass

        print(f"\n=== Video Stream Verification ===")
        print(f"Requested index: {video_source}")
        print(f"Actual camera: {actual_camera_name}")
        print(f"Resolution: {actual_width}x{actual_height}")
        print(f"FPS: {actual_fps}")
        print(f"Backend: {actual_backend}")

        # Warn if we got the wrong camera
        if selected_device:
            expected_name = selected_device.get('name', '')
            if expected_name and expected_name != actual_camera_name:
                print(f"\n⚠️  WARNING: Requested '{expected_name}' but opened '{actual_camera_name}'!")
                print(f"⚠️  OpenCV may have opened the wrong camera index!")
                print(f"⚠️  Try using --camera {video_source} explicitly or check camera connections.")

        print("=================================\n")

    # Apply optimizations based on source type
    if source_type in ["rtsp", "http"]:
        # Set OpenCV parameters for low-latency streaming
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimize buffer to reduce latency
    elif source_type == "camera" and isinstance(video_source, int) and video_source > 0:
        # Optimizations for external capture devices (Cam Link, etc.)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Reduce latency
        cap.set(cv2.CAP_PROP_FPS, 60)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))

        print(f"Applied optimizations for external capture device")
        print(f"  Buffer: Minimal for low latency")
        print(f"  Target FPS: 60\n")

    print("Press 'q' to quit the stream.")
    print(f"Publishing tracking state to KV store: {KV_STORE_NAME}")
    print(f"Key pattern: {entity_id}.detections.tracking_objects")
    print(f"Minimum frames before publishing: {args.min_frames}\n")

    # Track publishing statistics
    total_kv_updates = 0
    frame_count = 0
    total_detections = 0  # Track total detections across all frames

    # Setup OpenCV window with proper positioning and camera name
    camera_name = actual_camera_name if 'actual_camera_name' in locals() else device_fingerprint["camera"]["name"]
    window_title = f'Constellation ISR Tracking - {camera_name}'

    # Create window with proper flags for draggable and resizable behavior
    cv2.namedWindow(window_title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO | cv2.WINDOW_GUI_EXPANDED)

    # Set window to a reasonable size
    window_width = 1280
    window_height = 720
    cv2.resizeWindow(window_title, window_width, window_height)

    # Center window on screen (adjust for your display resolution)
    screen_width = 2560  # Adjust based on your display
    screen_height = 1440  # Adjust based on your display
    x_pos = max(50, (screen_width - window_width) // 2)
    y_pos = max(100, (screen_height - window_height) // 2)
    cv2.moveWindow(window_title, x_pos, y_pos)

    print(f"\nOpenCV Window Setup:")
    print(f"  Title: '{window_title}'")
    print(f"  Size: {window_width}x{window_height}")
    print(f"  Position: ({x_pos}, {y_pos})")
    print(f"  The window is draggable and resizable\n")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Error: Failed to capture frame.")
                break

            # Capture timestamp for this frame
            frame_timestamp = datetime.now(timezone.utc).isoformat()
            frame_count += 1
            tracking_state.total_frames_processed = frame_count

            # Run YOLOE tracking on the frame (using .track() instead of .predict())
            # persist=True maintains tracking IDs across frames
            results = model.track(
                frame,
                conf=confidence_threshold,
                verbose=False,
                persist=True,
                tracker=args.tracker
            )

            # Extract tracking results from the first (and only) result
            result = results[0]

            # Get image dimensions
            h, w = frame.shape[:2]

            # Track current frame's track IDs
            current_track_ids = set()

            if result.boxes is not None and len(result.boxes) > 0:
                # Get detection and tracking data
                boxes = result.boxes.xyxy.cpu().numpy()  # x1, y1, x2, y2 format
                confidences = result.boxes.conf.cpu().numpy()
                class_ids = result.boxes.cls.cpu().numpy().astype(int)

                # Count detections for this frame
                num_detections = len(boxes)
                total_detections += num_detections

                # Get tracking IDs (this is the key difference from detect_rtdetr.py)
                if result.boxes.id is not None:
                    track_ids = result.boxes.id.int().cpu().tolist()
                else:
                    # No tracking IDs available (shouldn't happen with .track())
                    track_ids = list(range(len(boxes)))
                    print(f"⚠️ Frame {frame_count}: No tracking IDs available for {num_detections} detections!")

                # Update tracking state for each detected object
                for box, conf, cls_id, track_id in zip(boxes, confidences, class_ids, track_ids):
                    x1, y1, x2, y2 = box

                    # Get class name from COCO classes
                    class_name = result.names[cls_id]

                    # Normalize coordinates
                    bbox = {
                        "x_min": float(x1 / w),
                        "y_min": float(y1 / h),
                        "x_max": float(x2 / w),
                        "y_max": float(y2 / h)
                    }

                    # Update tracking state
                    tracking_state.update_object(track_id, class_name, float(conf), bbox, frame_timestamp)
                    current_track_ids.add(track_id)

                    # Draw on frame (use absolute coordinates)
                    color = colors[cls_id % len(colors)]

                    # Draw bounding box
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)

                    # Get tracking info
                    obj_info = tracking_state.tracked_objects.get(track_id)
                    frame_count_str = f"[{obj_info['frame_count']}]" if obj_info else ""

                    # Draw label with confidence and tracking ID
                    label_text = f"ID:{track_id} {class_name} {conf:.2f} {frame_count_str}"
                    text_y = int(y1) - 10 if int(y1) - 10 > 10 else int(y1) + 20
                    cv2.putText(frame, label_text, (int(x1), text_y),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            # Mark objects that weren't seen in this frame as inactive
            tracking_state.mark_inactive_objects(current_track_ids)

            # Publish tracking state to KV store (only if we have persistent objects)
            persistent_objects = tracking_state.get_persistent_objects(min_frames=args.min_frames)
            if persistent_objects:
                await publish_tracking_state(kv, tracking_state, entity_id)
                total_kv_updates += 1
                # Log first successful publish
                if total_kv_updates == 1:
                    print(f"✓ First KV publish! Found {len(persistent_objects)} persistent objects")
            else:
                # Debug: show why we're not publishing
                if frame_count % 30 == 0 and analytics['tracked_objects_count'] > 0:
                    print(f"⏳ Frame {frame_count}: {analytics['tracked_objects_count']} tracked, but none persistent (need {args.min_frames}+ frames)")

            # Get analytics for display
            analytics = tracking_state.get_analytics()

            # Add status overlay with tracking info
            status_text = f"Device: {device_fingerprint['device_id'][:8]} | Active: {analytics['active_objects_count']} | Total Unique: {analytics['total_unique_objects']} | KV Updates: {total_kv_updates}"
            cv2.putText(frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            # Show label distribution
            label_dist = ", ".join([f"{k}:{v}" for k, v in analytics['label_distribution'].items()]) or "None"
            dist_text = f"Tracking: {label_dist}"
            cv2.putText(frame, dist_text, (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

            hostname_text = f"Host: {device_fingerprint['hostname']} | Model: YOLOE-11L | Tracker: {args.tracker.replace('.yaml', '').upper()}"
            cv2.putText(frame, hostname_text, (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

            # Display the frame with detections
            cv2.imshow(window_title, frame)

            # Exit on 'q' key press
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    finally:
        # Release resources
        print(f"\n=== Final Tracking Statistics ===")
        print(f"Total frames processed: {frame_count}")
        print(f"Total detections: {total_detections}")

        # Get final analytics
        final_analytics = tracking_state.get_analytics()
        print(f"Total unique objects tracked: {final_analytics['total_unique_objects']}")
        print(f"Total KV state updates: {total_kv_updates}")
        print(f"Label distribution: {final_analytics['label_distribution']}")

        # Show diagnostic info if no detections were published
        if total_kv_updates == 0:
            print(f"\n⚠️ No detections were published to KV store!")
            if total_detections == 0:
                print(f"   Reason: No objects detected in any frame")
                print(f"   Tip: Make sure objects are visible in camera view")
            elif final_analytics['total_unique_objects'] == 0:
                print(f"   Reason: Detections occurred but tracking failed")
                print(f"   Tip: Check tracker configuration")
            else:
                print(f"   Reason: Objects tracked but didn't persist for {args.min_frames}+ frames")
                print(f"   Tip: Try --min-frames 1 or ensure objects stay in frame longer")

        print("=================================\n")

        cap.release()
        cv2.destroyAllWindows()
        await cleanup()

if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
