"""SAM2 segmentation implementation."""

import numpy as np
import cv2
from typing import List, Dict, Any, Tuple

from .base import BaseDetector, load_ultralytics_model

class SAM2Detector(BaseDetector):
    """SAM2 automatic mask generation segmentation."""
    
    def __init__(self, args, model_config):
        super().__init__(args, model_config)
        self.imgsz = getattr(args, 'imgsz', 1024)
        self.colors = self._generate_colors()
        
    def _generate_colors(self) -> List[Tuple[int, int, int]]:
        """Generate colors for mask visualization."""
        np.random.seed(42)
        return [(int(c[0]), int(c[1]), int(c[2])) 
                for c in np.random.randint(0, 255, size=(100, 3))]
    
    async def load_model(self) -> None:
        """Load SAM2 model."""
        from ultralytics import SAM

        print("Loading SAM2 model...")
        print("NOTE: Using SAM2-B model for segmentation\n")
        self.model = load_ultralytics_model(
            SAM,
            self.model_config.model_file,
            "SAM2"
        )
        print(f"✓ SAM2 model loaded successfully")
        print(f"  Mode: Automatic mask generation (no prompts)")
        print(f"  Confidence threshold: {self.confidence_threshold}")
        print(f"  Image size: {self.imgsz}")
        print()
    
    def process_frame(self, frame: Any, frame_timestamp: str, 
                     frame_count: int) -> Tuple[List[Dict[str, Any]], Any]:
        """Process frame with SAM2 automatic mask generation."""
        # Run SAM2 automatic mask generation
        results = self.model.predict(
            frame,
            conf=self.confidence_threshold,
            imgsz=self.imgsz,
            verbose=False
        )
        
        result = results[0]
        h, w = frame.shape[:2]
        detections = []
        
        # Create overlay for masks
        overlay = frame.copy()
        
        if result.masks is not None and len(result.masks) > 0:
            masks = result.masks.data.cpu().numpy()
            
            # Get bounding boxes if available, otherwise compute from masks
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
                # Calculate mask area
                area = int(np.sum(mask))

                # Skip very small segments (noise)
                if area < 100:
                    continue

                # Create native segment ID (frame_count_idx for SAM2)
                native_id = f"{frame_count}_{idx}"

                # Get or create CUID using centralized service
                cuid = self.tracking_id_service.get_or_create_cuid(
                    native_id=native_id,
                    model_type=self.model_type
                )

                x1, y1, x2, y2 = box

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
                    label="segment",
                    confidence=float(conf),
                    bbox=bbox,
                    timestamp=frame_timestamp,
                    model_type=self.model_type,
                    native_id=native_id,
                    area=area,
                    mask=mask
                )

                detections.append(detection)
                
                # Visualize mask with semi-transparent overlay
                color = self.colors[idx % len(self.colors)]
                
                # Resize mask to frame size if needed
                if mask.shape != (h, w):
                    mask_resized = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
                else:
                    mask_resized = mask
                
                # Apply colored overlay
                overlay[mask_resized > 0.5] = color
                
                # Draw bounding box
                cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                
                # Draw label
                label_text = f"SEG:{segment_id} {conf:.2f} A:{area}"
                text_y = int(y1) - 10 if int(y1) - 10 > 10 else int(y1) + 20
                cv2.putText(frame, label_text, (int(x1), text_y),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
            
            # Blend overlay with original frame
            alpha = 0.4
            frame = cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0)
        
        return detections, frame
    
    def should_skip_small_detections(self, detection: Dict[str, Any]) -> bool:
        """Skip very small segments."""
        area = detection.get("area", 0)
        return area < 100
    
    def get_detection_color(self, detection: Dict[str, Any]) -> Tuple[int, int, int]:
        """Get color for segment."""
        segment_id = detection.get("track_id", 0)
        return self.colors[segment_id % len(self.colors)]
    
    def format_label_text(self, detection: Dict[str, Any], 
                         additional_info: str = "") -> str:
        """Format label text for segmentation."""
        segment_id = detection.get("track_id", "?")
        confidence = detection.get('confidence', 0.0)
        area = detection.get('area', 0)
        base_text = f"SEG:{segment_id} {confidence:.2f} A:{area}"
        
        if additional_info:
            return f"{base_text} {additional_info}"
        return base_text