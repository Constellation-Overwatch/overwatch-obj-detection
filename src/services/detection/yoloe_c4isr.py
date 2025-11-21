"""C4ISR Threat Detection using YOLOE with threat classification."""

import os
import shutil
from typing import List, Dict, Any, Tuple
from datetime import datetime, timezone

from .base import BaseDetector
from ...config.threats import ALL_CLASSES, CLASS_TO_THREAT_LEVEL, THREAT_CATEGORIES, add_custom_threat_class

class C4ISRThreatDetector(BaseDetector):
    """YOLOE detector with C4ISR threat classification."""

    def __init__(self, args, model_config):
        super().__init__(args, model_config)

        # Setup threat classes
        self._setup_threat_classes()
        
    def _setup_threat_classes(self):
        """Setup threat classification classes."""
        # Add custom threats if provided
        if self.args.custom_threats:
            for threat_class in self.args.custom_threats:
                add_custom_threat_class(threat_class, "MEDIUM_THREAT")
                print(f"Added custom threat class: {threat_class}")

    async def load_model(self) -> None:
        """Load YOLOE model with C4ISR threat prompts."""
        from ultralytics import YOLOE
        
        print("="*70)
        print("C4ISR THREAT DETECTION INITIALIZATION")
        print("="*70)
        print("Loading YOLOE model with open-vocabulary threat prompts...\n")
        
        # Setup model path
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        models_dir = os.path.join(script_dir, "models")
        model_path = os.path.join(models_dir, self.model_config.model_file)
        
        os.makedirs(models_dir, exist_ok=True)
        
        # Download model if needed
        if not os.path.exists(model_path):
            print(f"Model not found at {model_path}")
            print("Downloading YOLOE-11L-SEG model...")
            temp_model = YOLOE(self.model_config.model_file)
            default_model_path = os.path.expanduser(f"~/.ultralytics/weights/{self.model_config.model_file}")
            if os.path.exists(default_model_path):
                shutil.copy(default_model_path, model_path)
        
        # Load model
        if os.path.exists(model_path):
            print(f"Loading model from: {model_path}")
            self.model = YOLOE(model_path)
            print(f"✓ YOLOE model loaded successfully")
        else:
            print(f"Error: Could not load model")
            raise RuntimeError("Failed to load YOLOE model")
        
        # Setup MobileClip text encoder
        await self._setup_mobileclip(models_dir)
        
        # Configure text prompts
        print(f"Setting text prompts for YOLOE...")
        text_embeddings = self.model.get_text_pe(ALL_CLASSES)
        self.model.set_classes(ALL_CLASSES, text_embeddings)
        print(f"✓ Text prompts configured for {len(ALL_CLASSES)} classes")
        
        # Print threat categories
        print(f"\nThreat Categories:")
        for threat_level, config in THREAT_CATEGORIES.items():
            print(f"  {threat_level}: {len(config['classes'])} classes")
            print(f"    Examples: {', '.join(config['classes'][:3])}")
        
        print(f"\nConfidence threshold: {self.confidence_threshold}")
        print("="*70)
        print()
    
    async def _setup_mobileclip(self, models_dir: str):
        """Setup MobileClip text encoder."""
        mobileclip_local = os.path.join(models_dir, "mobileclip_blt.ts")
        mobileclip_cache = os.path.expanduser("~/.ultralytics/weights/mobileclip_blt.ts")
        
        # Ensure cache directory exists
        os.makedirs(os.path.dirname(mobileclip_cache), exist_ok=True)
        
        # Handle MobileClip placement
        if os.path.exists(mobileclip_local) and not os.path.exists(mobileclip_cache):
            shutil.copy(mobileclip_local, mobileclip_cache)
            print(f"✓ MobileClip available at {mobileclip_cache}")
        elif os.path.exists(mobileclip_cache) and not os.path.exists(mobileclip_local):
            shutil.copy(mobileclip_cache, mobileclip_local)
            print(f"✓ MobileClip available at {mobileclip_local}")
        elif not os.path.exists(mobileclip_cache):
            print(f"MobileClip will download on first use to {mobileclip_cache}")
    
    def process_frame(self, frame: Any, frame_timestamp: str,
                     frame_count: int) -> Tuple[List[Dict[str, Any]], Any]:
        """Process frame with YOLOE C4ISR threat detection."""
        # Run YOLOE with tracking enabled for persistent IDs
        results = self.model.track(
            frame,
            conf=self.confidence_threshold,
            verbose=False,
            persist=True,  # Maintain tracking IDs across frames
            tracker="bytetrack.yaml"  # Use ByteTrack for robust tracking
        )

        result = results[0]
        h, w = frame.shape[:2]
        detections = []
        current_track_ids = set()

        if result.boxes is not None and len(result.boxes) > 0:
            boxes = result.boxes.xyxy.cpu().numpy()
            confidences = result.boxes.conf.cpu().numpy()
            class_ids = result.boxes.cls.cpu().numpy().astype(int)

            # Get persistent tracking IDs
            if result.boxes.id is not None:
                track_ids = result.boxes.id.int().cpu().tolist()
            else:
                # Fallback to index-based IDs if tracking fails
                track_ids = list(range(len(boxes)))
                print(f"⚠️ Frame {frame_count}: No tracking IDs available for {len(boxes)} detections!")

            # Process each tracked detection
            for box, conf, cls_id, yolo_track_id in zip(boxes, confidences, class_ids, track_ids):
                x1, y1, x2, y2 = box

                # Get class name
                class_name = ALL_CLASSES[cls_id] if cls_id < len(ALL_CLASSES) else f"class_{cls_id}"

                # Determine threat level
                threat_level = CLASS_TO_THREAT_LEVEL.get(class_name, "NORMAL")

                # Get or create CUID using centralized service
                cuid = self.tracking_id_service.get_or_create_cuid(
                    native_id=yolo_track_id,
                    model_type=self.model_type
                )
                current_track_ids.add(cuid)

                # Normalize bbox
                bbox = {
                    "x_min": float(x1 / w),
                    "y_min": float(y1 / h),
                    "x_max": float(x2 / w),
                    "y_max": float(y2 / h)
                }

                # Calculate suspicious indicators
                suspicious_indicators = self._calculate_suspicious_indicators(
                    class_name, conf, threat_level
                )

                # Create standardized detection payload
                detection = self.tracking_id_service.format_detection_payload(
                    track_id=cuid,
                    label=class_name,
                    confidence=float(conf),
                    bbox=bbox,
                    timestamp=frame_timestamp,
                    model_type=self.model_type,
                    native_id=yolo_track_id,
                    # C4ISR-specific fields
                    threat_level=threat_level,
                    suspicious_indicators=suspicious_indicators
                )
                
                detections.append(detection)
        
        # Visualize detections with C4ISR styling
        frame = self._visualize_c4isr_detections(frame, detections)
        
        return detections, frame
    
    def _calculate_suspicious_indicators(self, label: str, confidence: float, 
                                       threat_level: str) -> List[str]:
        """Calculate suspicious indicators for threat assessment."""
        indicators = []
        
        if threat_level == "HIGH_THREAT" and confidence > 0.7:
            indicators.append("high_confidence_weapon_detection")
        elif threat_level == "MEDIUM_THREAT" and confidence > 0.5:
            indicators.append("suspicious_object_detected")
        elif threat_level == "HIGH_THREAT" and confidence < 0.5:
            indicators.append("uncertain_threat_requires_validation")
        
        return indicators
    
    def _visualize_c4isr_detections(self, frame: Any, detections: List[Dict[str, Any]]) -> Any:
        """Visualize detections with C4ISR threat styling."""
        import cv2
        h, w = frame.shape[:2]

        # Count threats for alert status
        threat_counts = {"HIGH_THREAT": 0, "MEDIUM_THREAT": 0}

        for detection in detections:
            threat_level = detection["metadata"]["threat_level"]
            if threat_level in threat_counts:
                threat_counts[threat_level] += 1

            # Get threat color and draw enhanced bounding box
            color = THREAT_CATEGORIES[threat_level]["color"]
            
            bbox = detection["bbox"]
            x1, y1 = int(bbox["x_min"] * w), int(bbox["y_min"] * h)
            x2, y2 = int(bbox["x_max"] * w), int(bbox["y_max"] * h)
            
            # Draw main bounding box
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
            
            # Draw corner markers for professional look
            corner_length = 20
            corner_thickness = 4
            
            # Top-left corner
            cv2.line(frame, (x1, y1), (x1 + corner_length, y1), color, corner_thickness)
            cv2.line(frame, (x1, y1), (x1, y1 + corner_length), color, corner_thickness)
            
            # Top-right corner
            cv2.line(frame, (x2, y1), (x2 - corner_length, y1), color, corner_thickness)
            cv2.line(frame, (x2, y1), (x2, y1 + corner_length), color, corner_thickness)
            
            # Bottom-left corner
            cv2.line(frame, (x1, y2), (x1 + corner_length, y2), color, corner_thickness)
            cv2.line(frame, (x1, y2), (x1, y2 - corner_length), color, corner_thickness)
            
            # Bottom-right corner
            cv2.line(frame, (x2, y2), (x2 - corner_length, y2), color, corner_thickness)
            cv2.line(frame, (x2, y2), (x2, y2 - corner_length), color, corner_thickness)
            
            # Draw label with threat level
            threat_label = threat_level.replace('_', ' ')
            label_text = f"[{threat_label}] {detection['label']} {detection['confidence']:.2f}"
            
            # Position label
            text_y = y1 - 10 if y1 - 10 > 10 else y1 + 15
            
            # Calculate text size for background
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.7
            font_thickness = 2
            (text_width, text_height), baseline = cv2.getTextSize(label_text, font, font_scale, font_thickness)
            
            # Draw label background
            padding = 5
            cv2.rectangle(
                frame,
                (x1, text_y - text_height - padding),
                (x1 + text_width + padding * 2, text_y + padding),
                color,
                -1
            )
            
            # Draw label text
            cv2.putText(frame, label_text, (x1 + padding, text_y),
                       font, font_scale, (255, 255, 255), font_thickness, cv2.LINE_AA)
        
        # Add threat status overlay
        self._add_threat_status_overlay(frame, threat_counts)
        
        return frame
    
    def _add_threat_status_overlay(self, frame: Any, threat_counts: Dict[str, int]) -> None:
        """Add C4ISR threat status overlay."""
        import cv2
        h, w = frame.shape[:2]
        
        # Determine alert level
        if threat_counts["HIGH_THREAT"] > 0:
            alert_color = (0, 0, 255)  # Red
            alert_text = "⚠ HIGH THREAT ALERT"
        elif threat_counts["MEDIUM_THREAT"] > 0:
            alert_color = (0, 165, 255)  # Orange
            alert_text = "⚠ MEDIUM THREAT"
        else:
            alert_color = (0, 255, 0)  # Green
            alert_text = "✓ NORMAL"
        
        # Status overlay background
        cv2.rectangle(frame, (0, 0), (w, 100), (0, 0, 0), -1)
        
        # Alert status
        cv2.putText(frame, alert_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, alert_color, 2)
        
        # Threat counts
        status_text = f"HIGH: {threat_counts['HIGH_THREAT']} | MED: {threat_counts['MEDIUM_THREAT']}"
        cv2.putText(frame, status_text, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    
    def get_detection_color(self, detection: Dict[str, Any]) -> Tuple[int, int, int]:
        """Get color for threat level."""
        threat_level = detection.get("metadata", {}).get("threat_level", "NORMAL")
        return THREAT_CATEGORIES[threat_level]["color"]