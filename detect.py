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

async def main():
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

    # Open webcam stream
    cap = cv2.VideoCapture(0)  # 0 is usually the default webcam

    if not cap.isOpened():
        print("Error: Could not open webcam.")
        await cleanup()
        exit()

    print("Press 'q' to quit the stream.")
    print(f"Publishing detection events to JetStream subject: {SUBJECT}")
    print(f"Using JetStream stream: {STREAM_NAME}")

    # Track publishing statistics
    total_published = 0

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

            # Display the frame with detections (include device ID in window title)
            window_title = f'Constellation ISR - Device: {device_fingerprint["device_id"][:8]}'
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
