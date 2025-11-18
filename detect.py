from transformers import AutoModelForCausalLM
from PIL import Image
import torch
import cv2
import asyncio
import nats
import json
from datetime import datetime, timezone
import signal
import sys
import platform
import socket
import hashlib
import uuid
import subprocess
import os
import argparse
import glob
from dotenv import load_dotenv

# Global NATS connection and JetStream context
nc = None
js = None
device_fingerprint = None  # Global device fingerprint
organization_id = None  # Organization identifier
entity_id = None  # Entity identifier

# Root constants for subject and stream construction
ROOT_SUBJECT = "constellation.events.isr"
ROOT_STREAM_NAME = "CONSTELLATION_EVENTS"

# Actual subject and stream - constructed dynamically with org_id and entity_id
SUBJECT = None
STREAM_NAME = None

def get_constellation_ids():
    """Get organization_id and entity_id from environment or user input"""
    # Load environment variables from .env file
    load_dotenv()

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

def get_device_fingerprint(org_id, ent_id):
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

    # Get camera information if available
    camera_info = get_camera_info()
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
        'type': 'moondream-object-detection',
        'version': '1.0.0',
        'model': 'vikhyatk/moondream2'
    }

    return fingerprint_data

def get_camera_info():
    """Get camera device information"""
    camera_info = {}

    # Try to get camera info based on platform
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

async def setup_nats():
    """Connect to NATS server and create JetStream context"""
    global nc, js, device_fingerprint, organization_id, entity_id, SUBJECT, STREAM_NAME

    # Get constellation identifiers first
    organization_id, entity_id = get_constellation_ids()

    # Construct subject with org_id and entity_id (stream name stays constant)
    SUBJECT = f"{ROOT_SUBJECT}.{organization_id}.{entity_id}"
    STREAM_NAME = ROOT_STREAM_NAME

    print(f"Configured NATS subject: {SUBJECT}")
    print(f"Configured stream name: {STREAM_NAME}\n")

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

    # Generate device fingerprint during bootsequence
    print("\n=== Bootsequence: Device Fingerprinting ===")
    device_fingerprint = get_device_fingerprint(organization_id, entity_id)
    print(f"Organization ID: {device_fingerprint['organization_id']}")
    print(f"Entity ID: {device_fingerprint['entity_id']}")
    print(f"Device ID: {device_fingerprint['device_id']}")
    print(f"Hostname: {device_fingerprint['hostname']}")
    print(f"Platform: {device_fingerprint['platform']['system']} {device_fingerprint['platform']['release']}")
    print(f"Camera: {device_fingerprint['camera']['name']}")
    print("=========================================\n")

    # Publish bootsequence event with device fingerprint
    bootsequence_message = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "bootsequence",
        "source": device_fingerprint,
        "message": "ISR component initialized with device fingerprint"
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

    return nc, js

async def publish_detection_event(js, detection_results, frame_timestamp):
    """Publish detection results to NATS JetStream"""
    global device_fingerprint

    if detection_results:
        message = {
            "timestamp": frame_timestamp,
            "event_type": "detection",
            "detections": detection_results,
            "count": len(detection_results),
            "source": device_fingerprint  # Use full device fingerprint metadata
        }

        try:
            # Use JetStream publish instead of regular NATS publish
            ack = await js.publish(
                SUBJECT,
                json.dumps(message).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Device-ID": device_fingerprint['device_id'],
                    "Event-Type": "detection"
                }
            )
            print(f"Published to JetStream: {len(detection_results)} object(s) detected")
            print(f"  Stream: {ack.stream}, Seq: {ack.seq}, Device: {device_fingerprint['device_id'][:8]}...")
        except nats.js.errors.NoStreamResponseError:
            print(f"Error: No JetStream stream available for subject {SUBJECT}")
            print("Ensure the stream is created with: nats stream add CONSTELLATION_EVENTS")
        except Exception as e:
            print(f"Error publishing to JetStream: {e}")

async def cleanup():
    """Clean up NATS connection and publish shutdown event"""
    global nc, js, device_fingerprint

    if js and device_fingerprint:
        # Publish shutdown event before closing
        shutdown_message = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "shutdown",
            "source": device_fingerprint,
            "message": "ISR component shutting down gracefully"
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

    # Suppress OpenCV warnings during enumeration
    if not verbose:
        cv2.setLogLevel(0)  # Suppress all OpenCV logging

    # Try indices 0-9
    for index in range(10):
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            # Get device info
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = int(cap.get(cv2.CAP_PROP_FPS))
            backend = cap.getBackendName()

            # Determine if this is a built-in camera
            is_native = (platform.system() == 'Darwin' and index == 0 and backend == 'AVFOUNDATION')

            devices.append({
                'index': index,
                'type': 'local_camera',
                'resolution': f"{width}x{height}",
                'fps': fps,
                'backend': backend,
                'is_native': is_native,
                'name': 'FaceTime HD Camera (Built-in)' if is_native else f'Camera {index}'
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
    parser = argparse.ArgumentParser(description='Constellation ISR Object Detection Client')

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

    # Connect to NATS and JetStream
    nc, js = await setup_nats()

    # Load the model
    print("Loading Moondream model...")
    model = AutoModelForCausalLM.from_pretrained(
        "vikhyatk/moondream2",
        trust_remote_code=True,
        dtype=torch.bfloat16,
        device_map="mps", # "cuda" on Nvidia GPUs
        local_files_only=True,  # Use cached model, don't download from HuggingFace
    )
    print("Model loaded successfully")

    # Query for detection (you can modify this as needed)
    prompt = "Objects"

    if prompt.strip():
        object_prompt = f"List all {prompt.strip()} you can see in this image. Return your answer as a simple comma-separated list of object names."
    else:
        object_prompt = "List all the objects you can see in this image. Return your answer as a simple comma-separated list of object names."

    # Optional sampling settings for detection
    settings = {"max_objects": 50}

    # Define colors for bounding boxes (cycle through for different labels)
    colors = [(0, 255, 0), (255, 0, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255)]

    # Determine video source based on arguments
    video_source = None
    source_type = None

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
                print("  2. Run: uv run -m detect --list-devices")
                print("  3. Or remove --skip-native to use built-in camera")
                await cleanup()
                sys.exit(1)

        if devices:
            selected = devices[0]
            video_source = selected.get('index', selected.get('path', 0))
            source_type = "camera"
            print(f"Found {len(devices)} device(s)")
            print(f"Selected: {selected.get('name', 'Unknown')}")
            print(f"  Index: {selected.get('index', selected.get('path', 'N/A'))}")
            print(f"  Resolution: {selected.get('resolution', 'N/A')}")
            print(f"  FPS: {selected.get('fps', 'N/A')}")
            print("===================================\n")
        else:
            # No cameras found at all
            if args.skip_native:
                print("\nError: No cameras detected!")
                print("  1. Connect a camera/capture device")
                print("  2. Run: uv run -m detect --list-devices")
            else:
                # Fallback to default camera index 0
                video_source = 0
                source_type = "camera"
                print(f"No devices detected, trying default camera (index 0)")
            print("===================================\n")
            if args.skip_native:
                await cleanup()
                sys.exit(1)

    # Open video stream
    cap = cv2.VideoCapture(video_source)

    # Apply optimizations based on source type
    if source_type in ["rtsp", "http"]:
        # Set OpenCV parameters for low-latency streaming
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimize buffer to reduce latency
    elif source_type == "camera" and isinstance(video_source, int) and video_source > 0:
        # Optimizations for external capture devices (Cam Link, etc.)
        # These devices typically have hardware buffers, so minimize software buffering
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Reduce latency

        # Try to maximize frame rate for external devices
        # Cam Link 4K supports 60fps at 1080p
        cap.set(cv2.CAP_PROP_FPS, 60)

        # Enable hardware acceleration if available
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))

        print(f"Applied optimizations for external capture device")
        print(f"  Buffer: Minimal for low latency")
        print(f"  Target FPS: 60")

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
            print("  2. Run: uv run -m detect --list-devices")
            print("  3. Try a different camera index")
        await cleanup()
        exit()

    print("Press 'q' to quit the stream.")
    print(f"Publishing detection events to JetStream subject: {SUBJECT}")
    print(f"Using JetStream stream: {STREAM_NAME}")

    # Track publishing statistics
    total_published = 0

    # Setup OpenCV window with proper positioning
    window_title = f'Constellation ISR - Device: {device_fingerprint["device_id"][:8]}'
    cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)  # Resizable window

    # Position window at top-left of screen (easily visible and movable)
    cv2.moveWindow(window_title, 100, 100)

    # Set window to a reasonable size (adjust based on your display)
    # For 1080p capture, scale down to ~720p for easier viewing
    cv2.resizeWindow(window_title, 1280, 720)

    print(f"\nWindow positioned at (100, 100) with size 1280x720")
    print(f"You can resize and move the window as needed\n")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Error: Failed to capture frame.")
                break

            # Capture timestamp for this frame
            frame_timestamp = datetime.now(timezone.utc).isoformat()

            # Convert OpenCV frame (BGR) to PIL Image (RGB)
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

            # Step 1: Query for objects
            query_result = model.query(image, object_prompt)
            object_list = query_result["answer"] or ""

            # Parse the comma-separated response
            objects = [obj.strip() for obj in object_list.split(',') if obj.strip()]

            # Step 2: Detect bounding boxes for each object
            detection_results = []
            for object_name in objects:
                detect_result = model.detect(image, object_name, settings=settings)
                for obj in detect_result.get("objects", []):
                    detection_results.append({
                        "label": object_name,
                        "x_min": obj["x_min"],
                        "y_min": obj["y_min"],
                        "x_max": obj["x_max"],
                        "y_max": obj["y_max"]
                    })

            # Publish detection event to JetStream if objects were detected
            if detection_results:
                await publish_detection_event(js, detection_results, frame_timestamp)
                total_published += 1

            # Get image dimensions for denormalizing bounding boxes
            h, w = frame.shape[:2]

            # Assign consistent colors per label
            label_to_color = {}
            color_index = 0
            for res in detection_results:
                label = res["label"]
                if label not in label_to_color:
                    label_to_color[label] = colors[color_index % len(colors)]
                    color_index += 1

            # Step 3: Draw bounding boxes and labels on the frame
            for res in detection_results:
                label = res["label"]
                color = label_to_color[label]

                x1 = int(res["x_min"] * w)
                y1 = int(res["y_min"] * h)
                x2 = int(res["x_max"] * w)
                y2 = int(res["y_max"] * h)

                # Draw rectangle
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                # Draw label (above the box if possible, else below)
                text_y = y1 - 10 if y1 - 10 > 10 else y1 + 20
                cv2.putText(frame, label, (x1, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            # Add status overlay with device info
            status_text = f"Device: {device_fingerprint['device_id'][:8]} | Published: {total_published}"
            cv2.putText(frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            hostname_text = f"Host: {device_fingerprint['hostname']}"
            cv2.putText(frame, hostname_text, (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

            # Display the frame with detections
            cv2.imshow(window_title, frame)

            # Exit on 'q' key press
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    finally:
        # Release resources
        print(f"\nTotal events published to JetStream: {total_published}")
        cap.release()
        cv2.destroyAllWindows()
        await cleanup()

if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
