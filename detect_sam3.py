import os
from dotenv import load_dotenv

# Load .env for Constellation configuration
load_dotenv()

# SAM2 from Ultralytics (SAM3 upgrade path when available)
# TODO: Replace with SAM3VideoPredictor when Meta releases model weights
from ultralytics import SAM
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

# Suppress OpenCV logging globally
os.environ['OPENCV_LOG_LEVEL'] = 'SILENT'
os.environ['OPENCV_VIDEOIO_DEBUG'] = '0'
cv2.setLogLevel(0)

# Global NATS connection, JetStream context, and KV store
nc = None
js = None
kv = None
device_fingerprint = None
organization_id = None
entity_id = None

# KV Store name for global state
KV_STORE_NAME = "CONSTELLATION_GLOBAL_STATE"

# Root constants for subject and stream construction
ROOT_SUBJECT = "constellation.events.isr"
ROOT_STREAM_NAME = "CONSTELLATION_EVENTS"

# Actual subject and stream - constructed dynamically
SUBJECT = None
STREAM_NAME = None

# Segmentation tracking state
class SegmentationState:
    """Manages state for segmented objects"""
    def __init__(self):
        self.segmented_objects = {}  # segment_id -> object metadata
        self.total_unique_segments = 0
        self.total_frames_processed = 0
        self.active_segment_ids = set()

    def update_segment(self, segment_id, mask, bbox, area, confidence, frame_timestamp):
        """Update or create segmented object state"""
        if segment_id not in self.segmented_objects:
            # New segment detected
            self.total_unique_segments += 1
            self.segmented_objects[segment_id] = {
                "segment_id": segment_id,
                "first_seen": frame_timestamp,
                "last_seen": frame_timestamp,
                "frame_count": 1,
                "total_confidence": confidence,
                "avg_confidence": confidence,
                "area": area,
                "bbox": bbox,
                "is_active": True
            }
        else:
            # Update existing segment
            obj = self.segmented_objects[segment_id]
            obj["last_seen"] = frame_timestamp
            obj["frame_count"] += 1
            obj["total_confidence"] += confidence
            obj["avg_confidence"] = obj["total_confidence"] / obj["frame_count"]
            obj["area"] = area
            obj["bbox"] = bbox
            obj["is_active"] = True

        self.active_segment_ids.add(segment_id)

    def mark_inactive_segments(self, current_segment_ids):
        """Mark segments that weren't seen in this frame as inactive"""
        for segment_id in list(self.segmented_objects.keys()):
            if segment_id not in current_segment_ids:
                self.segmented_objects[segment_id]["is_active"] = False
                if segment_id in self.active_segment_ids:
                    self.active_segment_ids.remove(segment_id)

    def get_persistent_segments(self, min_frames=3):
        """Get segments that have been tracked for at least min_frames"""
        return {
            sid: obj for sid, obj in self.segmented_objects.items()
            if obj["frame_count"] >= min_frames
        }

    def get_analytics(self):
        """Get segmentation analytics summary"""
        active_segments = [obj for obj in self.segmented_objects.values() if obj["is_active"]]

        return {
            "total_unique_segments": self.total_unique_segments,
            "total_frames_processed": self.total_frames_processed,
            "active_segments_count": len(active_segments),
            "tracked_segments_count": len(self.segmented_objects),
            "active_segment_ids": list(self.active_segment_ids)
        }

# Global segmentation state
segmentation_state = SegmentationState()

def get_constellation_ids():
    """Get organization_id and entity_id from environment or user input"""
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
    """Generate a comprehensive device fingerprint with metadata"""
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

    # Get camera information
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

    # ISR component metadata
    # NOTE: Currently using SAM2 - will upgrade to SAM3 when Meta releases weights
    fingerprint_data['component'] = {
        'name': 'constellation-isr',
        'type': 'sam-segmentation',
        'version': '1.0.0',
        'model': 'sam2_b.pt',  # TODO: Update to sam3.pt when available
        'mode': 'automatic-mask-generation'
    }

    return fingerprint_data

async def setup_nats(selected_device=None):
    """Connect to NATS server and create JetStream context + KV store"""
    global nc, js, kv, device_fingerprint, organization_id, entity_id, SUBJECT, STREAM_NAME

    # Get constellation identifiers
    organization_id, entity_id = get_constellation_ids()

    # Construct subject with org_id and entity_id
    SUBJECT = f"{ROOT_SUBJECT}.{organization_id}.{entity_id}"
    STREAM_NAME = ROOT_STREAM_NAME

    print(f"Configured NATS subject: {SUBJECT}")
    print(f"Configured stream name: {STREAM_NAME}")
    print(f"Configured KV store: {KV_STORE_NAME}\n")

    nc = await nats.connect("nats://localhost:4222")
    print("Connected to NATS server")

    # Create JetStream context
    js = nc.jetstream()

    # Verify stream exists
    try:
        stream_info = await js.stream_info(STREAM_NAME)
        print(f"Connected to JetStream stream: {STREAM_NAME}")
        print(f"Stream subjects: {stream_info.config.subjects}")
    except Exception as e:
        print(f"Warning: Stream {STREAM_NAME} not found. Messages may not be persisted.")
        print(f"Error: {e}")

    # Create or connect to KV store
    try:
        kv = await js.create_key_value(config=KeyValueConfig(
            bucket=KV_STORE_NAME,
            description="Constellation global state for segmentation tracking",
            history=10,
            ttl=3600
        ))
        print(f"Created/connected to KV store: {KV_STORE_NAME}")
    except Exception as e:
        try:
            kv = await js.key_value(KV_STORE_NAME)
            print(f"Connected to existing KV store: {KV_STORE_NAME}")
        except Exception as e2:
            print(f"Error accessing KV store: {e2}")
            print("Continuing without KV store - state will not be persisted")

    # Generate device fingerprint
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

    # Publish bootsequence event
    bootsequence_message = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "bootsequence",
        "source": device_fingerprint,
        "message": "ISR component initialized with SAM2 segmentation"
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

async def publish_segmentation_state(kv, segmentation_state, entity_id):
    """Publish segmentation state to NATS KV store"""
    if not kv:
        return

    try:
        # Get persistent segments
        persistent_segments = segmentation_state.get_persistent_segments(min_frames=3)

        # Get analytics
        analytics = segmentation_state.get_analytics()

        # Prepare state data
        state_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entity_id": entity_id,
            "device_id": device_fingerprint['device_id'],
            "analytics": analytics,
            "segmented_objects": {
                str(sid): {
                    "segment_id": obj["segment_id"],
                    "first_seen": obj["first_seen"],
                    "last_seen": obj["last_seen"],
                    "frame_count": obj["frame_count"],
                    "avg_confidence": obj["avg_confidence"],
                    "is_active": obj["is_active"],
                    "area": obj["area"],
                    "bbox": obj["bbox"]
                }
                for sid, obj in persistent_segments.items()
            }
        }

        # Store in KV
        key = f"{entity_id}.detections.segmentation_objects"
        await kv.put(key, json.dumps(state_data).encode())

        # Store analytics separately
        analytics_key = f"{entity_id}.analytics.summary"
        await kv.put(analytics_key, json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entity_id": entity_id,
            **analytics
        }).encode())

    except Exception as e:
        print(f"Error publishing segmentation state to KV: {e}")

async def cleanup():
    """Clean up NATS connection and publish shutdown event"""
    global nc, js, kv, device_fingerprint

    if js and device_fingerprint:
        shutdown_message = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "shutdown",
            "source": device_fingerprint,
            "message": "ISR component shutting down gracefully",
            "final_analytics": segmentation_state.get_analytics()
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

    if not verbose:
        cv2.setLogLevel(3)

    return devices

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description='Constellation ISR Segmentation Client (SAM2/SAM3)')

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
                             help='HTTP stream URL')

    # Legacy RTSP options
    parser.add_argument('--rtsp-ip', type=str, default=None,
                       help='RTSP stream IP address (legacy)')
    parser.add_argument('--rtsp-port', type=int, default=8554,
                       help='RTSP stream port (default: 8554)')
    parser.add_argument('--rtsp-path', type=str, default='/live/stream',
                       help='RTSP stream path (default: /live/stream)')

    # Additional options
    parser.add_argument('--skip-native', action='store_true',
                       help='Skip built-in/native cameras during auto-detection')

    # Segmentation options
    parser.add_argument('--min-frames', type=int, default=3,
                       help='Minimum frames to track before publishing (default: 3)')
    parser.add_argument('--conf', type=float, default=0.25,
                       help='Confidence threshold (default: 0.25)')
    parser.add_argument('--imgsz', type=int, default=1024,
                       help='Input image size (default: 1024)')

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

    # Set up signal handler
    signal.signal(signal.SIGINT, signal_handler)

    # =========================================================================
    # STEP 1: Determine video source
    # =========================================================================
    video_source = None
    source_type = None
    selected_device = None

    if args.camera is not None:
        video_source = args.camera
        source_type = "camera"
        print(f"\n=== Camera Mode ===")
        print(f"Using camera index: {args.camera}")
        print("===================\n")

    elif args.device:
        video_source = args.device
        source_type = "device"
        print(f"\n=== Device Mode ===")
        print(f"Using device: {args.device}")
        print("===================\n")

    elif args.rtsp:
        video_source = args.rtsp
        source_type = "rtsp"
        print(f"\n=== RTSP Stream Mode ===")
        print(f"Connecting to: {args.rtsp}")
        print("========================\n")

    elif args.http:
        video_source = args.http
        source_type = "http"
        print(f"\n=== HTTP Stream Mode ===")
        print(f"Connecting to: {args.http}")
        print("========================\n")

    elif args.rtsp_ip:
        rtsp_url = f"rtsp://{args.rtsp_ip}:{args.rtsp_port}{args.rtsp_path}"
        video_source = rtsp_url
        source_type = "rtsp"
        print(f"\n=== RTSP Stream Mode ===")
        print(f"Connecting to: {rtsp_url}")
        print("========================\n")

    else:
        # Auto-detect
        print("\n=== Auto-detecting video source ===")
        devices = enumerate_video_devices()

        if args.skip_native and devices:
            non_native = [d for d in devices if not d.get('is_native', False)]
            if non_native:
                devices = non_native
                print("Skipping native/built-in cameras...")
            else:
                print("\nError: --skip-native specified but no external cameras found!")
                print("\nAvailable devices:")
                for dev in devices:
                    print(f"  - {dev.get('name', 'Unknown')} (native: {dev.get('is_native', False)})")
                print("\nPlease:")
                print("  1. Connect an external camera/capture device")
                print("  2. Run: uv run detect_sam3.py --list-devices")
                print("  3. Or remove --skip-native to use built-in camera")
                sys.exit(1)

        if devices:
            selected_device = devices[0]
            video_source = selected_device.get('index', selected_device.get('path', 0))
            source_type = "camera"
            print(f"Found {len(devices)} device(s)")
            print(f"Selected: {selected_device.get('name', 'Unknown')}")
            print(f"  Index: {selected_device.get('index', selected_device.get('path', 'N/A'))}")
            print(f"  Resolution: {selected_device.get('resolution', 'N/A')}")
            print(f"  FPS: {selected_device.get('fps', 'N/A')}")
            print("===================================\n")
        else:
            if args.skip_native:
                print("\nError: No cameras detected!")
                print("  1. Connect a camera/capture device")
                print("  2. Run: uv run detect_sam3.py --list-devices")
                print("===================================\n")
                sys.exit(1)
            else:
                video_source = 0
                source_type = "camera"
                print(f"No devices detected, trying default camera (index 0)")
                print("===================================\n")

    # =========================================================================
    # STEP 2: Connect to NATS
    # =========================================================================
    nc, js, kv = await setup_nats(selected_device)

    # =========================================================================
    # STEP 3: Load SAM2 model
    # =========================================================================
    print("Loading SAM2 model...")
    print("NOTE: Will upgrade to SAM3 when Meta releases model weights\n")

    # Get the project root directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    models_dir = os.path.join(script_dir, "models")
    model_path = os.path.join(models_dir, "sam2_b.pt")

    # Ensure models directory exists
    os.makedirs(models_dir, exist_ok=True)

    # Check if model exists locally
    if not os.path.exists(model_path):
        print(f"Model not found at {model_path}")
        print("Downloading SAM2-B model...")

        # Let Ultralytics download to cache
        import shutil
        temp_model = SAM("sam2_b.pt")

        # Find in cache and copy
        default_model_path = os.path.expanduser("~/.ultralytics/weights/sam2_b.pt")
        if os.path.exists(default_model_path):
            shutil.copy(default_model_path, model_path)
            print(f"Model cached to: {model_path}")
        else:
            print(f"Warning: Could not find model in cache at {default_model_path}")
            alt_paths = [
                os.path.expanduser("~/.cache/ultralytics/weights/sam2_b.pt"),
                os.path.join(os.getcwd(), "sam2_b.pt")
            ]
            for alt_path in alt_paths:
                if os.path.exists(alt_path):
                    shutil.copy(alt_path, model_path)
                    print(f"Model cached to: {model_path} (from {alt_path})")
                    break

    # Load model
    if os.path.exists(model_path):
        print(f"Loading model from: {model_path}")
        model = SAM(model_path)
        print(f"✓ SAM2 model loaded successfully")
        print(f"  Mode: Automatic mask generation (no prompts)")
        print(f"  Confidence threshold: {args.conf}")
        print(f"  Image size: {args.imgsz}\n")
    else:
        print(f"Error: Could not load or download model to {model_path}")
        await cleanup()
        sys.exit(1)

    # Generate colors for mask visualization
    np.random.seed(42)
    colors = [(int(c[0]), int(c[1]), int(c[2])) for c in np.random.randint(0, 255, size=(100, 3))]

    # Open video stream
    if source_type == "camera" and isinstance(video_source, int) and platform.system() == 'Darwin':
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
            print("  2. Run: uv run detect_sam3.py --list-devices")
            print("  3. Try a different camera index")
        await cleanup()
        exit()

    # Apply optimizations
    if source_type in ["rtsp", "http"]:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    elif source_type == "camera" and isinstance(video_source, int) and video_source > 0:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FPS, 60)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
        print(f"Applied optimizations for external capture device")
        print(f"  Buffer: Minimal for low latency")
        print(f"  Target FPS: 60\n")

    print("Press 'q' to quit the stream.")
    print(f"Publishing segmentation state to KV store: {KV_STORE_NAME}")
    print(f"Key pattern: {entity_id}.detections.segmentation_objects")
    print(f"Minimum frames before publishing: {args.min_frames}\n")

    # Track statistics
    total_kv_updates = 0
    frame_count = 0
    total_segments = 0

    # Setup OpenCV window with proper centering
    camera_name = device_fingerprint["camera"]["name"]
    window_title = f'Constellation ISR Segmentation - {camera_name}'

    # Create window with normal flag (resizable and draggable)
    cv2.namedWindow(window_title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO | cv2.WINDOW_GUI_EXPANDED)

    # Set window size
    window_width = 1280
    window_height = 720
    cv2.resizeWindow(window_title, window_width, window_height)

    # Center window on screen
    # For macOS, estimate screen size (most common: 2560x1440 or 1920x1080)
    screen_width = 2560  # Estimate for typical macOS display
    screen_height = 1440

    # Calculate centered position
    x_pos = max(50, (screen_width - window_width) // 2)
    y_pos = max(100, (screen_height - window_height) // 2)

    # Move window to centered position
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

            # Capture timestamp
            frame_timestamp = datetime.now(timezone.utc).isoformat()
            frame_count += 1
            segmentation_state.total_frames_processed = frame_count

            # Run SAM2 automatic mask generation
            # NOTE: No prompts = automatic segmentation of entire frame
            results = model.predict(
                frame,
                conf=args.conf,
                imgsz=args.imgsz,
                verbose=False
            )

            # Extract segmentation results
            result = results[0]

            # Get image dimensions
            h, w = frame.shape[:2]

            # Track current frame's segment IDs
            current_segment_ids = set()

            # Create overlay for masks
            overlay = frame.copy()

            if result.masks is not None and len(result.masks) > 0:
                masks = result.masks.data.cpu().numpy()
                num_segments = len(masks)
                total_segments += num_segments

                # If we have boxes, use them; otherwise compute from masks
                if result.boxes is not None and len(result.boxes) > 0:
                    boxes = result.boxes.xyxy.cpu().numpy()
                    confidences = result.boxes.conf.cpu().numpy()
                else:
                    # Compute bounding boxes from masks
                    boxes = []
                    confidences = []
                    for mask in masks:
                        # Find contours
                        mask_uint8 = (mask * 255).astype(np.uint8)
                        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        if contours:
                            x, y, w_box, h_box = cv2.boundingRect(contours[0])
                            boxes.append([x, y, x + w_box, y + h_box])
                            confidences.append(0.9)  # Default confidence
                        else:
                            boxes.append([0, 0, 0, 0])
                            confidences.append(0.0)
                    boxes = np.array(boxes)
                    confidences = np.array(confidences)

                # Process each segment
                for idx, (mask, box, conf) in enumerate(zip(masks, boxes, confidences)):
                    segment_id = idx  # Simple ID based on detection order

                    # Calculate mask area
                    area = int(np.sum(mask))

                    # Skip very small segments (noise)
                    if area < 100:
                        continue

                    x1, y1, x2, y2 = box

                    # Normalize bbox coordinates
                    bbox = {
                        "x_min": float(x1 / w),
                        "y_min": float(y1 / h),
                        "x_max": float(x2 / w),
                        "y_max": float(y2 / h)
                    }

                    # Update segmentation state
                    segmentation_state.update_segment(segment_id, mask, bbox, area, float(conf), frame_timestamp)
                    current_segment_ids.add(segment_id)

                    # Visualize mask with semi-transparent overlay
                    color = colors[idx % len(colors)]

                    # Resize mask to frame size if needed
                    if mask.shape != (h, w):
                        mask_resized = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
                    else:
                        mask_resized = mask

                    # Apply colored overlay
                    overlay[mask_resized > 0.5] = color

                    # Draw bounding box
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)

                    # Get segment info
                    obj_info = segmentation_state.segmented_objects.get(segment_id)
                    frame_count_str = f"[{obj_info['frame_count']}]" if obj_info else ""

                    # Draw label
                    label_text = f"SEG:{segment_id} {conf:.2f} {frame_count_str} A:{area}"
                    text_y = int(y1) - 10 if int(y1) - 10 > 10 else int(y1) + 20
                    cv2.putText(frame, label_text, (int(x1), text_y),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

                # Blend overlay with original frame
                alpha = 0.4
                frame = cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0)

            # Mark inactive segments
            segmentation_state.mark_inactive_segments(current_segment_ids)

            # Publish to KV store
            persistent_segments = segmentation_state.get_persistent_segments(min_frames=args.min_frames)
            if persistent_segments:
                await publish_segmentation_state(kv, segmentation_state, entity_id)
                total_kv_updates += 1
                if total_kv_updates == 1:
                    print(f"✓ First KV publish! Found {len(persistent_segments)} persistent segments")
            else:
                if frame_count % 30 == 0:
                    analytics = segmentation_state.get_analytics()
                    if analytics['tracked_segments_count'] > 0:
                        print(f"⏳ Frame {frame_count}: {analytics['tracked_segments_count']} tracked, but none persistent (need {args.min_frames}+ frames)")

            # Get analytics for display
            analytics = segmentation_state.get_analytics()

            # Add status overlay
            status_text = f"Device: {device_fingerprint['device_id'][:8]} | Active: {analytics['active_segments_count']} | Total Unique: {analytics['total_unique_segments']} | KV Updates: {total_kv_updates}"
            cv2.putText(frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            hostname_text = f"Host: {device_fingerprint['hostname']} | Model: SAM2-B | Mode: Auto-Mask-Gen"
            cv2.putText(frame, hostname_text, (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

            # Display frame
            cv2.imshow(window_title, frame)

            # Exit on 'q'
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    finally:
        # Release resources
        print(f"\n=== Final Segmentation Statistics ===")
        print(f"Total frames processed: {frame_count}")
        print(f"Total segments: {total_segments}")

        # Get final analytics
        final_analytics = segmentation_state.get_analytics()
        print(f"Total unique segments tracked: {final_analytics['total_unique_segments']}")
        print(f"Total KV state updates: {total_kv_updates}")

        # Show diagnostic info
        if total_kv_updates == 0:
            print(f"\n⚠️ No segments were published to KV store!")
            if total_segments == 0:
                print(f"   Reason: No segments detected in any frame")
                print(f"   Tip: Make sure objects are visible in camera view")
            elif final_analytics['total_unique_segments'] == 0:
                print(f"   Reason: Segments occurred but tracking failed")
            else:
                print(f"   Reason: Segments tracked but didn't persist for {args.min_frames}+ frames")
                print(f"   Tip: Try --min-frames 1")

        print("=====================================\n")

        cap.release()
        cv2.destroyAllWindows()
        await cleanup()

if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
