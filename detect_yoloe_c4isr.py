import os
from dotenv import load_dotenv

# Load .env for Constellation configuration
load_dotenv()

# YOLOE from Ultralytics with open-vocabulary prompt capabilities for C4ISR
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
from typing import Dict, List, Set

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

# ============================================================================
# C4ISR THREAT CLASSIFICATION CONFIGURATION
# ============================================================================

# Define threat categories for C4ISR operations
THREAT_CATEGORIES = {
    "HIGH_THREAT": {
        "classes": ["weapon", "knife", "gun", "rifle", "pistol", "explosive", "bomb"],
        "color": (0, 0, 255),  # Red
        "priority": 1
    },
    "MEDIUM_THREAT": {
        "classes": ["suspicious package", "unattended bag", "backpack", "suitcase",
                   "unauthorized vehicle", "truck", "van"],
        "color": (0, 165, 255),  # Orange
        "priority": 2
    },
    "LOW_THREAT": {
        "classes": ["person", "car", "bicycle", "motorcycle", "dog"],
        "color": (0, 255, 255),  # Yellow
        "priority": 3
    },
    "NORMAL": {
        "classes": ["traffic light", "stop sign", "bench", "bird", "cat"],
        "color": (0, 255, 0),  # Green
        "priority": 4
    }
}

# Build comprehensive class list for YOLOE prompts
ALL_CLASSES = []
CLASS_TO_THREAT_LEVEL = {}
for threat_level, config in THREAT_CATEGORIES.items():
    for cls in config["classes"]:
        ALL_CLASSES.append(cls)
        CLASS_TO_THREAT_LEVEL[cls] = threat_level

# ============================================================================
# OBJECT TRACKING STATE WITH C4ISR THREAT INTELLIGENCE
# ============================================================================

class C4ISRTrackingState:
    """Manages state for tracked objects with C4ISR threat intelligence"""
    def __init__(self):
        self.tracked_objects = {}  # track_id -> object metadata
        self.total_unique_objects = 0
        self.total_frames_processed = 0
        self.active_track_ids = set()

        # C4ISR threat analytics
        self.threat_alerts = []  # List of threat alerts
        self.threat_summary = {
            "HIGH_THREAT": 0,
            "MEDIUM_THREAT": 0,
            "LOW_THREAT": 0,
            "NORMAL": 0
        }

    def update_object(self, track_id, label, confidence, bbox, frame_timestamp, threat_level):
        """Update or create tracked object state with threat intelligence"""
        if track_id not in self.tracked_objects:
            # New object detected
            self.total_unique_objects += 1

            # Create threat alert for high/medium threats
            if threat_level in ["HIGH_THREAT", "MEDIUM_THREAT"]:
                alert = {
                    "alert_id": f"{track_id}_{frame_timestamp}",
                    "track_id": track_id,
                    "label": label,
                    "threat_level": threat_level,
                    "confidence": confidence,
                    "first_detected": frame_timestamp,
                    "bbox": bbox,
                    "status": "active"
                }
                self.threat_alerts.append(alert)

            self.tracked_objects[track_id] = {
                "track_id": track_id,
                "label": label,
                "threat_level": threat_level,
                "first_seen": frame_timestamp,
                "last_seen": frame_timestamp,
                "frame_count": 1,
                "total_confidence": confidence,
                "avg_confidence": confidence,
                "max_confidence": confidence,
                "bbox_history": [bbox],
                "is_active": True,
                "suspicious_indicators": self._calculate_suspicious_indicators(label, confidence, threat_level)
            }
        else:
            # Update existing object
            obj = self.tracked_objects[track_id]
            obj["last_seen"] = frame_timestamp
            obj["frame_count"] += 1
            obj["total_confidence"] += confidence
            obj["avg_confidence"] = obj["total_confidence"] / obj["frame_count"]
            obj["max_confidence"] = max(obj["max_confidence"], confidence)
            obj["bbox_history"].append(bbox)
            obj["is_active"] = True
            obj["suspicious_indicators"] = self._calculate_suspicious_indicators(label, confidence, threat_level)

            # Keep only last 30 frames of bbox history
            if len(obj["bbox_history"]) > 30:
                obj["bbox_history"] = obj["bbox_history"][-30:]

        self.active_track_ids.add(track_id)

    def _calculate_suspicious_indicators(self, label, confidence, threat_level):
        """Calculate suspicious indicators for threat assessment"""
        indicators = []

        # High confidence threats are more suspicious
        if threat_level == "HIGH_THREAT" and confidence > 0.7:
            indicators.append("high_confidence_weapon_detection")

        # Medium confidence threats warrant investigation
        if threat_level == "MEDIUM_THREAT" and confidence > 0.5:
            indicators.append("suspicious_object_detected")

        # Low confidence high threats are uncertain
        if threat_level == "HIGH_THREAT" and confidence < 0.5:
            indicators.append("uncertain_threat_requires_validation")

        return indicators

    def mark_inactive_objects(self, current_track_ids):
        """Mark objects that weren't seen in this frame as inactive"""
        for track_id in list(self.tracked_objects.keys()):
            if track_id not in current_track_ids:
                self.tracked_objects[track_id]["is_active"] = False
                if track_id in self.active_track_ids:
                    self.active_track_ids.remove(track_id)

    def get_persistent_objects(self, min_frames=3):
        """Get objects that have been tracked for at least min_frames"""
        return {
            tid: obj for tid, obj in self.tracked_objects.items()
            if obj["frame_count"] >= min_frames
        }

    def get_analytics(self):
        """Get tracking analytics with C4ISR threat intelligence"""
        active_objects = [obj for obj in self.tracked_objects.values() if obj["is_active"]]

        # Count by label and threat level
        label_counts = defaultdict(int)
        threat_counts = defaultdict(int)

        for obj in active_objects:
            label_counts[obj["label"]] += 1
            threat_counts[obj["threat_level"]] += 1

        # Get active threats (HIGH and MEDIUM only)
        active_threats = [
            obj for obj in active_objects
            if obj["threat_level"] in ["HIGH_THREAT", "MEDIUM_THREAT"]
        ]

        return {
            "total_unique_objects": self.total_unique_objects,
            "total_frames_processed": self.total_frames_processed,
            "active_objects_count": len(active_objects),
            "tracked_objects_count": len(self.tracked_objects),
            "label_distribution": dict(label_counts),
            "threat_distribution": dict(threat_counts),
            "active_threat_count": len(active_threats),
            "active_track_ids": list(self.active_track_ids),
            "threat_alerts": self.threat_alerts[-10:]  # Last 10 alerts
        }

# Global tracking state
tracking_state = C4ISRTrackingState()

def get_constellation_ids():
    """Get organization_id and entity_id from environment or user input"""
    print("\n=== Constellation Configuration ===")
    print("Initializing Constellation Overwatch Edge Awareness connection...")
    print()

    org_id = os.environ.get('CONSTELLATION_ORG_ID')
    if not org_id:
        print("Organization ID not found in environment (CONSTELLATION_ORG_ID)")
        org_id = input("Enter Organization ID: ").strip()
        if not org_id:
            print("Error: Organization ID is required")
            sys.exit(1)
    else:
        print(f"Organization ID loaded from environment: {org_id}")

    ent_id = os.environ.get('CONSTELLATION_ENTITY_ID')
    if not ent_id:
        print("Entity ID not found in environment (CONSTELLATION_ENTITY_ID)")
        ent_id = input("Enter Entity ID: ").strip()
        if not ent_id:
            print("Error: Entity ID is required")
            sys.exit(1)
    else:
        print(f"Entity ID loaded from environment: {ent_id}")

    print("===================================\n")
    return org_id, ent_id

def get_device_fingerprint(org_id, ent_id, selected_device=None):
    """Generate device fingerprint with C4ISR metadata"""
    fingerprint_data = {}

    fingerprint_data['organization_id'] = org_id
    fingerprint_data['entity_id'] = ent_id

    fingerprint_data['hostname'] = socket.gethostname()
    fingerprint_data['platform'] = {
        'system': platform.system(),
        'release': platform.release(),
        'version': platform.version(),
        'machine': platform.machine(),
        'processor': platform.processor(),
        'python_version': platform.python_version()
    }

    try:
        fingerprint_data['ip_address'] = socket.gethostbyname(socket.gethostname())
        fingerprint_data['fqdn'] = socket.getfqdn()
    except:
        fingerprint_data['ip_address'] = 'unknown'
        fingerprint_data['fqdn'] = 'unknown'

    try:
        mac = uuid.getnode()
        fingerprint_data['mac_address'] = ':'.join(['{:02x}'.format((mac >> i) & 0xff) for i in range(0, 48, 8)][::-1])
    except:
        fingerprint_data['mac_address'] = 'unknown'

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

    fingerprint_data['user'] = os.environ.get('USER', 'unknown')
    fingerprint_data['home'] = os.environ.get('HOME', 'unknown')

    hardware_string = f"{fingerprint_data['hostname']}-{fingerprint_data['mac_address']}-{platform.machine()}"
    fingerprint_data['device_id'] = hashlib.sha256(hardware_string.encode()).hexdigest()[:16]

    fingerprint_data['fingerprinted_at'] = datetime.now(timezone.utc).isoformat()

    # C4ISR component metadata
    fingerprint_data['component'] = {
        'name': 'constellation-isr-c4isr',
        'type': 'yoloe-open-vocab-threat-detection',
        'version': '1.0.0',
        'model': 'yoloe-11l-seg',
        'mode': 'text-prompt',
        'mission': 'C4ISR',
        'capabilities': ['threat-detection', 'object-tracking', 'confidence-scoring', 'threat-classification']
    }

    return fingerprint_data

async def setup_nats(selected_device=None):
    """Connect to NATS server and create JetStream context + KV store"""
    global nc, js, kv, device_fingerprint, organization_id, entity_id, SUBJECT, STREAM_NAME

    organization_id, entity_id = get_constellation_ids()

    SUBJECT = f"{ROOT_SUBJECT}.{organization_id}.{entity_id}"
    STREAM_NAME = ROOT_STREAM_NAME

    print(f"Configured NATS subject: {SUBJECT}")
    print(f"Configured stream name: {STREAM_NAME}")
    print(f"Configured KV store: {KV_STORE_NAME}\n")

    nc = await nats.connect("nats://localhost:4222")
    print("Connected to NATS server")

    js = nc.jetstream()

    try:
        stream_info = await js.stream_info(STREAM_NAME)
        print(f"Connected to JetStream stream: {STREAM_NAME}")
        print(f"Stream subjects: {stream_info.config.subjects}")
    except Exception as e:
        print(f"Warning: Stream {STREAM_NAME} not found.")
        print(f"Error: {e}")

    try:
        kv = await js.create_key_value(config=KeyValueConfig(
            bucket=KV_STORE_NAME,
            description="Constellation C4ISR threat intelligence and object tracking",
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
            print("Continuing without KV store")

    print("\n=== Bootsequence: Device Fingerprinting ===")
    device_fingerprint = get_device_fingerprint(organization_id, entity_id, selected_device)
    print(f"Organization ID: {device_fingerprint['organization_id']}")
    print(f"Entity ID: {device_fingerprint['entity_id']}")
    print(f"Device ID: {device_fingerprint['device_id']}")
    print(f"Mission: {device_fingerprint['component']['mission']}")
    print(f"Capabilities: {', '.join(device_fingerprint['component']['capabilities'])}")
    print("=========================================\n")

    # Publish bootsequence event
    bootsequence_message = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "bootsequence",
        "source": device_fingerprint,
        "message": "C4ISR ISR component initialized with YOLOE open-vocabulary threat detection"
    }

    try:
        ack = await js.publish(
            SUBJECT,
            json.dumps(bootsequence_message).encode(),
            headers={
                "Content-Type": "application/json",
                "Event-Type": "bootsequence",
                "Mission": "C4ISR"
            }
        )
        print(f"Published bootsequence event to JetStream")
        print(f"  Stream: {ack.stream}, Seq: {ack.seq}")
    except Exception as e:
        print(f"Error publishing bootsequence: {e}")

    return nc, js, kv

async def publish_detection_event(js, detection_data, entity_id):
    """Publish individual detection event to JetStream stream"""
    if not js:
        return

    try:
        detection_event = {
            "timestamp": detection_data["timestamp"],
            "event_type": "detection",
            "entity_id": entity_id,
            "device_id": device_fingerprint['device_id'],
            "detection": {
                "track_id": detection_data["track_id"],
                "label": detection_data["label"],
                "confidence": detection_data["confidence"],
                "threat_level": detection_data["threat_level"],
                "bbox": detection_data["bbox"],
                "suspicious_indicators": detection_data["suspicious_indicators"]
            }
        }

        await js.publish(
            SUBJECT,
            json.dumps(detection_event).encode(),
            headers={
                "Content-Type": "application/json",
                "Event-Type": "detection",
                "Threat-Level": detection_data["threat_level"],
                "Label": detection_data["label"]
            }
        )
    except Exception as e:
        print(f"Error publishing detection event: {e}")

async def publish_threat_intelligence(kv, tracking_state, entity_id):
    """Publish C4ISR threat intelligence to NATS KV store"""
    if not kv:
        return

    try:
        persistent_objects = tracking_state.get_persistent_objects(min_frames=3)
        analytics = tracking_state.get_analytics()

        # Prepare C4ISR threat intelligence data
        threat_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entity_id": entity_id,
            "device_id": device_fingerprint['device_id'],
            "mission": "C4ISR",
            "analytics": analytics,
            "threat_summary": {
                "total_threats": analytics.get("active_threat_count", 0),
                "threat_distribution": analytics.get("threat_distribution", {}),
                "alert_level": "HIGH" if analytics.get("threat_distribution", {}).get("HIGH_THREAT", 0) > 0 else "NORMAL"
            },
            "tracked_objects": {
                str(tid): {
                    "track_id": obj["track_id"],
                    "label": obj["label"],
                    "threat_level": obj["threat_level"],
                    "first_seen": obj["first_seen"],
                    "last_seen": obj["last_seen"],
                    "frame_count": obj["frame_count"],
                    "avg_confidence": obj["avg_confidence"],
                    "max_confidence": obj["max_confidence"],
                    "is_active": obj["is_active"],
                    "suspicious_indicators": obj["suspicious_indicators"],
                    "current_bbox": obj["bbox_history"][-1] if obj["bbox_history"] else None
                }
                for tid, obj in persistent_objects.items()
            },
            "threat_alerts": analytics.get("threat_alerts", [])
        }

        # Store threat intelligence in KV
        key = f"{entity_id}.c4isr.threat_intelligence"
        await kv.put(key, json.dumps(threat_data).encode())

        # Store analytics separately
        analytics_key = f"{entity_id}.analytics.c4isr_summary"
        await kv.put(analytics_key, json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entity_id": entity_id,
            **analytics
        }).encode())

    except Exception as e:
        print(f"Error publishing threat intelligence to KV: {e}")

async def cleanup():
    """Clean up NATS connection and publish shutdown event"""
    global nc, js, kv, device_fingerprint

    if js and device_fingerprint:
        shutdown_message = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "shutdown",
            "source": device_fingerprint,
            "message": "C4ISR ISR component shutting down gracefully",
            "final_analytics": tracking_state.get_analytics()
        }

        try:
            ack = await js.publish(
                SUBJECT,
                json.dumps(shutdown_message).encode(),
                headers={
                    "Content-Type": "application/json",
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

    if not verbose:
        cv2.setLogLevel(0)

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
    parser = argparse.ArgumentParser(description='Constellation C4ISR Threat Detection Client (YOLOE)')

    # Video source options
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument('--list-devices', action='store_true',
                             help='List available video devices and exit')
    source_group.add_argument('--camera', type=int, default=None,
                             help='Camera device index')
    source_group.add_argument('--device', type=str, default=None,
                             help='Device path')
    source_group.add_argument('--rtsp', type=str, default=None,
                             help='RTSP URL')
    source_group.add_argument('--http', type=str, default=None,
                             help='HTTP stream URL')

    # Legacy RTSP options
    parser.add_argument('--rtsp-ip', type=str, default=None,
                       help='RTSP stream IP address (legacy)')
    parser.add_argument('--rtsp-port', type=int, default=8554,
                       help='RTSP stream port')
    parser.add_argument('--rtsp-path', type=str, default='/live/stream',
                       help='RTSP stream path')

    # Additional options
    parser.add_argument('--skip-native', action='store_true',
                       help='Skip built-in/native cameras')

    # C4ISR options
    parser.add_argument('--min-frames', type=int, default=1,
                       help='Minimum frames to track before publishing (default: 1 for immediate threat alerts)')
    parser.add_argument('--conf', type=float, default=0.25,
                       help='Confidence threshold (default: 0.25)')
    parser.add_argument('--custom-threats', type=str, nargs='+', default=None,
                       help='Additional threat classes to detect')

    return parser.parse_args()

async def main():
    args = parse_args()

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

    signal.signal(signal.SIGINT, signal_handler)

    # Determine video source
    video_source = None
    source_type = None
    selected_device = None

    if args.camera is not None:
        video_source = args.camera
        source_type = "camera"
    elif args.device:
        video_source = args.device
        source_type = "device"
    elif args.rtsp:
        video_source = args.rtsp
        source_type = "rtsp"
    elif args.http:
        video_source = args.http
        source_type = "http"
    elif args.rtsp_ip:
        rtsp_url = f"rtsp://{args.rtsp_ip}:{args.rtsp_port}{args.rtsp_path}"
        video_source = rtsp_url
        source_type = "rtsp"
    else:
        # Auto-detect
        devices = enumerate_video_devices()
        if args.skip_native and devices:
            non_native = [d for d in devices if not d.get('is_native', False)]
            if non_native:
                devices = non_native
            else:
                print("\nError: --skip-native specified but no external cameras found!")
                sys.exit(1)

        if devices:
            selected_device = devices[0]
            video_source = selected_device.get('index', selected_device.get('path', 0))
            source_type = "camera"
        else:
            video_source = 0
            source_type = "camera"

    # Connect to NATS
    nc, js, kv = await setup_nats(selected_device)

    # =========================================================================
    # Load YOLOE model with C4ISR text prompts
    # =========================================================================
    print("="*70)
    print("C4ISR THREAT DETECTION INITIALIZATION")
    print("="*70)
    print("Loading YOLOE model with open-vocabulary threat prompts...\n")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    models_dir = os.path.join(script_dir, "models")
    model_path = os.path.join(models_dir, "yoloe-11l-seg.pt")

    os.makedirs(models_dir, exist_ok=True)

    if not os.path.exists(model_path):
        print(f"Model not found at {model_path}")
        print("Downloading YOLOE-11L-SEG model...")
        import shutil
        temp_model = YOLOE("yoloe-11l-seg.pt")
        default_model_path = os.path.expanduser("~/.ultralytics/weights/yoloe-11l-seg.pt")
        if os.path.exists(default_model_path):
            shutil.copy(default_model_path, model_path)

    if os.path.exists(model_path):
        print(f"Loading model from: {model_path}")
        model = YOLOE(model_path)

        # Add custom threat classes if provided
        if args.custom_threats:
            for threat_class in args.custom_threats:
                if threat_class not in ALL_CLASSES:
                    ALL_CLASSES.append(threat_class)
                    CLASS_TO_THREAT_LEVEL[threat_class] = "MEDIUM_THREAT"  # Default to medium

        # Configure YOLOE with C4ISR threat prompts
        print(f"\n✓ YOLOE model loaded successfully")
        print(f"  Configuring open-vocabulary threat detection...")
        print(f"  Total threat classes: {len(ALL_CLASSES)}")
        print(f"\n  Threat Categories:")
        for threat_level, config in THREAT_CATEGORIES.items():
            print(f"    {threat_level}: {len(config['classes'])} classes")
            print(f"      Examples: {', '.join(config['classes'][:3])}")

        # Ensure mobileclip text encoder is also in models/ directory
        mobileclip_path = os.path.join(models_dir, "mobileclip_blt.ts")
        if not os.path.exists(mobileclip_path):
            print(f"\n  MobileClip text encoder not found at {mobileclip_path}")
            print(f"  Downloading MobileClip for text prompt embeddings...")
            # Trigger download by calling get_text_pe with a dummy class
            # This will download mobileclip to ~/.ultralytics/weights/
            try:
                import shutil
                _ = model.get_text_pe(["initialization"])  # Trigger download
                default_mobileclip = os.path.expanduser("~/.ultralytics/weights/mobileclip_blt.ts")
                if os.path.exists(default_mobileclip):
                    shutil.copy(default_mobileclip, mobileclip_path)
                    print(f"  ✓ MobileClip copied to {mobileclip_path}")
            except Exception as e:
                print(f"  Warning: Could not copy mobileclip model: {e}")
        else:
            print(f"\n  ✓ MobileClip text encoder found at {mobileclip_path}")

        # Set classes using YOLOE's text prompt API
        print(f"\n  Setting text prompts for YOLOE...")
        text_embeddings = model.get_text_pe(ALL_CLASSES)
        model.set_classes(ALL_CLASSES, text_embeddings)
        print(f"  ✓ Text prompts configured for {len(ALL_CLASSES)} classes")

        print(f"\n  Confidence threshold: {args.conf}")
        print(f"  Minimum frames for threat alert: {args.min_frames}")
        print("="*70)
        print()
    else:
        print(f"Error: Could not load model")
        await cleanup()
        sys.exit(1)

    # Open video stream
    if source_type == "camera" and isinstance(video_source, int) and platform.system() == 'Darwin':
        cap = cv2.VideoCapture(video_source, cv2.CAP_AVFOUNDATION)
    else:
        cap = cv2.VideoCapture(video_source)

    if not cap.isOpened():
        print(f"Error: Could not open video source: {video_source}")
        await cleanup()
        exit()

    # Apply optimizations
    if source_type in ["rtsp", "http"]:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    elif source_type == "camera" and isinstance(video_source, int) and video_source > 0:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FPS, 60)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))

    print("Press 'q' to quit the stream.")
    print(f"Publishing threat intelligence to KV store: {KV_STORE_NAME}")
    print(f"Key pattern: {entity_id}.c4isr.threat_intelligence\n")

    # Track statistics
    total_kv_updates = 0
    frame_count = 0
    total_detections = 0
    total_threats_detected = 0

    # Setup OpenCV window with proper centering
    camera_name = device_fingerprint["camera"]["name"]
    window_title = f'C4ISR Threat Detection - {camera_name}'

    # Create window with normal flag (resizable and draggable)
    cv2.namedWindow(window_title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO | cv2.WINDOW_GUI_EXPANDED)

    # Set window size
    window_width = 1280
    window_height = 720
    cv2.resizeWindow(window_title, window_width, window_height)

    # Center window on screen
    # For macOS, estimate screen size (most common: 2560x1440 or 1920x1080)
    # Position window in center-left area that's accessible
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

            frame_timestamp = datetime.now(timezone.utc).isoformat()
            frame_count += 1
            tracking_state.total_frames_processed = frame_count

            # Run YOLOE with text prompts (no tracking, pure detection)
            results = model.predict(
                frame,
                conf=args.conf,
                verbose=False
            )

            result = results[0]
            h, w = frame.shape[:2]

            # Track current frame's detections
            current_track_ids = set()

            if result.boxes is not None and len(result.boxes) > 0:
                boxes = result.boxes.xyxy.cpu().numpy()
                confidences = result.boxes.conf.cpu().numpy()
                class_ids = result.boxes.cls.cpu().numpy().astype(int)

                num_detections = len(boxes)
                total_detections += num_detections

                # Process each detection
                for idx, (box, conf, cls_id) in enumerate(zip(boxes, confidences, class_ids)):
                    x1, y1, x2, y2 = box

                    # Get class name from configured prompts
                    class_name = ALL_CLASSES[cls_id] if cls_id < len(ALL_CLASSES) else f"class_{cls_id}"

                    # Determine threat level
                    threat_level = CLASS_TO_THREAT_LEVEL.get(class_name, "NORMAL")

                    # Create simple track ID based on frame and detection
                    track_id = f"{frame_count}_{idx}"

                    # Count threats
                    if threat_level in ["HIGH_THREAT", "MEDIUM_THREAT"]:
                        total_threats_detected += 1

                    # Normalize bbox
                    bbox = {
                        "x_min": float(x1 / w),
                        "y_min": float(y1 / h),
                        "x_max": float(x2 / w),
                        "y_max": float(y2 / h)
                    }

                    # Update tracking state with threat intelligence
                    tracking_state.update_object(track_id, class_name, float(conf), bbox, frame_timestamp, threat_level)
                    current_track_ids.add(track_id)

                    # Get object info for suspicious indicators
                    obj_info = tracking_state.tracked_objects.get(track_id)

                    # Publish individual detection event to stream
                    await publish_detection_event(js, {
                        "timestamp": frame_timestamp,
                        "track_id": track_id,
                        "label": class_name,
                        "confidence": float(conf),
                        "threat_level": threat_level,
                        "bbox": bbox,
                        "suspicious_indicators": obj_info.get("suspicious_indicators", []) if obj_info else []
                    }, entity_id)

                    # Get threat color
                    color = THREAT_CATEGORIES[threat_level]["color"]

                    # Draw main bounding box with rounded corners effect
                    box_thickness = 3
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, box_thickness)

                    # Draw corner markers for better visibility (professional look)
                    corner_length = 20
                    corner_thickness = 4

                    # Top-left corner
                    cv2.line(frame, (int(x1), int(y1)), (int(x1) + corner_length, int(y1)), color, corner_thickness)
                    cv2.line(frame, (int(x1), int(y1)), (int(x1), int(y1) + corner_length), color, corner_thickness)

                    # Top-right corner
                    cv2.line(frame, (int(x2), int(y1)), (int(x2) - corner_length, int(y1)), color, corner_thickness)
                    cv2.line(frame, (int(x2), int(y1)), (int(x2), int(y1) + corner_length), color, corner_thickness)

                    # Bottom-left corner
                    cv2.line(frame, (int(x1), int(y2)), (int(x1) + corner_length, int(y2)), color, corner_thickness)
                    cv2.line(frame, (int(x1), int(y2)), (int(x1), int(y2) - corner_length), color, corner_thickness)

                    # Bottom-right corner
                    cv2.line(frame, (int(x2), int(y2)), (int(x2) - corner_length, int(y2)), color, corner_thickness)
                    cv2.line(frame, (int(x2), int(y2)), (int(x2), int(y2) - corner_length), color, corner_thickness)

                    # Draw label with threat level
                    threat_label = threat_level.replace('_', ' ')
                    label_text = f"[{threat_label}] {class_name} {conf:.2f}"

                    # Calculate text size
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    font_scale = 0.7
                    font_thickness = 2
                    (text_width, text_height), baseline = cv2.getTextSize(label_text, font, font_scale, font_thickness)

                    # Position label above box (or below if at top of frame)
                    label_y = int(y1) - 10
                    if label_y - text_height - 10 < 0:
                        label_y = int(y1) + text_height + 15

                    # Draw label background with padding
                    padding = 5
                    cv2.rectangle(
                        frame,
                        (int(x1), label_y - text_height - padding),
                        (int(x1) + text_width + padding * 2, label_y + padding),
                        color,
                        -1
                    )

                    # Draw label text
                    cv2.putText(
                        frame,
                        label_text,
                        (int(x1) + padding, label_y),
                        font,
                        font_scale,
                        (255, 255, 255),
                        font_thickness,
                        cv2.LINE_AA
                    )

            # Mark inactive objects
            tracking_state.mark_inactive_objects(current_track_ids)

            # Publish threat intelligence to KV store
            persistent_objects = tracking_state.get_persistent_objects(min_frames=args.min_frames)
            if persistent_objects:
                await publish_threat_intelligence(kv, tracking_state, entity_id)
                total_kv_updates += 1
                if total_kv_updates == 1:
                    print(f"✓ First threat intelligence publish! Found {len(persistent_objects)} persistent objects")

            # Get analytics for display
            analytics = tracking_state.get_analytics()

            # Add threat status overlay
            threat_dist = analytics.get("threat_distribution", {})
            high_threats = threat_dist.get("HIGH_THREAT", 0)
            medium_threats = threat_dist.get("MEDIUM_THREAT", 0)

            # Determine alert level color
            if high_threats > 0:
                alert_color = (0, 0, 255)  # Red
                alert_text = "⚠ HIGH THREAT ALERT"
            elif medium_threats > 0:
                alert_color = (0, 165, 255)  # Orange
                alert_text = "⚠ MEDIUM THREAT"
            else:
                alert_color = (0, 255, 0)  # Green
                alert_text = "✓ NORMAL"

            # Status overlay
            cv2.rectangle(frame, (0, 0), (w, 100), (0, 0, 0), -1)  # Black background

            # Alert status
            cv2.putText(frame, alert_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, alert_color, 2)

            # Threat counts
            status_text = f"HIGH: {high_threats} | MED: {medium_threats} | Active: {analytics['active_objects_count']} | Total: {analytics['total_unique_objects']}"
            cv2.putText(frame, status_text, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            # System info
            sys_text = f"Device: {device_fingerprint['device_id'][:8]} | Frame: {frame_count} | KV Updates: {total_kv_updates}"
            cv2.putText(frame, sys_text, (10, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

            # Display frame
            cv2.imshow(window_title, frame)

            # Exit on 'q'
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    finally:
        # Release resources
        print(f"\n=== Final C4ISR Threat Intelligence Statistics ===")
        print(f"Total frames processed: {frame_count}")
        print(f"Total detections: {total_detections}")
        print(f"Total threats detected: {total_threats_detected}")

        final_analytics = tracking_state.get_analytics()
        print(f"Total unique objects tracked: {final_analytics['total_unique_objects']}")
        print(f"Threat distribution: {final_analytics.get('threat_distribution', {})}")
        print(f"Total KV threat intelligence updates: {total_kv_updates}")
        print(f"Total threat alerts generated: {len(tracking_state.threat_alerts)}")
        print("="*50)
        print()

        cap.release()
        cv2.destroyAllWindows()
        await cleanup()

if __name__ == "__main__":
    asyncio.run(main())
