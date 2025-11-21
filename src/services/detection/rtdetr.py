"""RT-DETR object detection implementation."""

import os
import shutil
import numpy as np
from typing import List, Dict, Any, Tuple

from .base import BaseDetector

class RTDETRDetector(BaseDetector):
    """RT-DETR real-time object detection."""
    
    def __init__(self, args, model_config):
        super().__init__(args, model_config)
        self.colors = self._generate_colors()
        
    def _generate_colors(self) -> List[Tuple[int, int, int]]:
        """Generate colors for COCO classes."""
        np.random.seed(42)
        return [(int(c[0]), int(c[1]), int(c[2])) 
                for c in np.random.randint(0, 255, size=(80, 3))]
    
    async def load_model(self) -> None:
        """Load RT-DETR model."""
        from ultralytics import RTDETR
        
        print("Loading RT-DETR model...")
        
        # Setup model path
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        models_dir = os.path.join(script_dir, "models")
        model_path = os.path.join(models_dir, self.model_config.model_file)
        
        os.makedirs(models_dir, exist_ok=True)
        
        # Download model if needed
        if not os.path.exists(model_path):
            print(f"Model not found at {model_path}")
            print("Downloading RT-DETR-l model...")
            temp_model = RTDETR(self.model_config.model_file)
            default_model_path = os.path.expanduser(f"~/.ultralytics/weights/{self.model_config.model_file}")
            if os.path.exists(default_model_path):
                shutil.copy(default_model_path, model_path)
                print(f"Model saved to: {model_path}")
        
        # Load model
        if os.path.exists(model_path):
            print(f"Loading model from: {model_path}")
            self.model = RTDETR(model_path)
            print(f"✓ RT-DETR model loaded successfully")
            print(f"  Confidence threshold: {self.confidence_threshold}")
            print()
        else:
            print(f"Error: Could not load RT-DETR model")
            raise RuntimeError("Failed to load RT-DETR model")
    
    def process_frame(self, frame: Any, frame_timestamp: str,
                     frame_count: int) -> Tuple[List[Dict[str, Any]], Any]:
        """Process frame with RT-DETR detection."""
        # Run RT-DETR inference
        results = self.model(frame, conf=self.confidence_threshold, verbose=False)
        result = results[0]
        h, w = frame.shape[:2]

        detections = []

        if result.boxes is not None and len(result.boxes) > 0:
            boxes = result.boxes.xyxy.cpu().numpy()  # x1, y1, x2, y2
            confidences = result.boxes.conf.cpu().numpy()
            class_ids = result.boxes.cls.cpu().numpy().astype(int)

            # Process each detection
            for idx, (box, conf, cls_id) in enumerate(zip(boxes, confidences, class_ids)):
                x1, y1, x2, y2 = box

                # Get class name from COCO classes
                class_name = result.names[cls_id]

                # Create native detection ID (frame_count_idx for RT-DETR)
                native_id = f"{frame_count}_{idx}"

                # Get or create CUID using centralized service
                cuid = self.tracking_id_service.get_or_create_cuid(
                    native_id=native_id,
                    model_type=self.model_type
                )

                # Normalize bbox coordinates
                bbox = {
                    "x_min": float(x1 / w),
                    "y_min": float(y1 / h),
                    "x_max": float(x2 / w),
                    "y_max": float(y2 / h)
                }

                # Create standardized detection payload
                detection = self.tracking_id_service.format_detection_payload(
                    track_id=cuid,
                    label=class_name,
                    confidence=float(conf),
                    bbox=bbox,
                    timestamp=frame_timestamp,
                    model_type=self.model_type,
                    native_id=native_id
                )

                detections.append(detection)

        # Visualize detections
        frame = self._visualize_detections(frame, detections)

        return detections, frame
    
    def _visualize_detections(self, frame: Any, detections: List[Dict[str, Any]]) -> Any:
        """Visualize RT-DETR detections."""
        import cv2
        h, w = frame.shape[:2]
        
        for detection in detections:
            # Get class ID for color
            cls_id = hash(detection["label"]) % len(self.colors)
            color = self.colors[cls_id]
            
            bbox = detection["bbox"]
            x1, y1 = int(bbox["x_min"] * w), int(bbox["y_min"] * h)
            x2, y2 = int(bbox["x_max"] * w), int(bbox["y_max"] * h)
            
            # Draw bounding box
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            # Draw label
            label_text = f"{detection['label']} {detection['confidence']:.2f}"
            text_y = y1 - 10 if y1 - 10 > 10 else y1 + 20
            cv2.putText(frame, label_text, (x1, text_y),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        
        return frame
    
    def get_detection_color(self, detection: Dict[str, Any]) -> Tuple[int, int, int]:
        """Get color for detection based on class."""
        cls_id = hash(detection["label"]) % len(self.colors)
        return self.colors[cls_id]