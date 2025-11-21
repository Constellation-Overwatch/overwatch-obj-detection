"""Moondream text-based object detection implementation."""

import torch
from PIL import Image
import cv2
from typing import List, Dict, Any, Tuple

from .base import BaseDetector

class MoondreamDetector(BaseDetector):
    """Moondream text-based object detection."""
    
    def __init__(self, args, model_config):
        super().__init__(args, model_config)
        self.prompt = getattr(args, 'prompt', 'Objects')
        self.max_objects = getattr(args, 'max_objects', 50)
        self.colors = self._generate_colors()
        
    def _generate_colors(self) -> List[Tuple[int, int, int]]:
        """Generate colors for object visualization."""
        return [(0, 255, 0), (255, 0, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255)]
    
    async def load_model(self) -> None:
        """Load Moondream model."""
        from transformers import AutoModelForCausalLM
        
        print("Loading Moondream model...")
        
        try:
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_config.model_file,  # "vikhyatk/moondream2"
                trust_remote_code=True,
                dtype=torch.bfloat16,
                device_map="mps",  # "cuda" on Nvidia GPUs, "cpu" if no GPU
                local_files_only=True  # Use cached model
            )
            print("✓ Moondream model loaded successfully")
            print(f"  Detection prompt: '{self.prompt}'")
            print(f"  Max objects: {self.max_objects}")
            print()
        except Exception as e:
            print(f"Error loading Moondream model: {e}")
            print("Make sure the model is downloaded and cached locally")
            raise RuntimeError("Failed to load Moondream model")
    
    def process_frame(self, frame: Any, frame_timestamp: str, 
                     frame_count: int) -> Tuple[List[Dict[str, Any]], Any]:
        """Process frame with Moondream text-based detection."""
        # Convert OpenCV frame (BGR) to PIL Image (RGB)
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        
        # Create detection prompt
        if self.prompt.strip():
            object_prompt = f"List all {self.prompt.strip()} you can see in this image. Return your answer as a simple comma-separated list of object names."
        else:
            object_prompt = "List all the objects you can see in this image. Return your answer as a simple comma-separated list of object names."
        
        # Step 1: Query for objects
        query_result = self.model.query(image, object_prompt)
        object_list = query_result.get("answer", "") or ""
        
        # Parse the comma-separated response
        objects = [obj.strip() for obj in object_list.split(',') if obj.strip()]
        
        # Step 2: Detect bounding boxes for each object
        detections = []
        settings = {"max_objects": self.max_objects}
        
        for object_name in objects:
            detect_result = self.model.detect(image, object_name, settings=settings)
            for obj in detect_result.get("objects", []):
                # Create native detection ID (frame_count_idx for Moondream)
                native_id = f"{frame_count}_{len(detections)}"

                # Get or create CUID using centralized service
                cuid = self.tracking_id_service.get_or_create_cuid(
                    native_id=native_id,
                    model_type=self.model_type
                )

                # Normalize bbox coordinates (already normalized from Moondream)
                bbox = {
                    "x_min": obj["x_min"],
                    "y_min": obj["y_min"],
                    "x_max": obj["x_max"],
                    "y_max": obj["y_max"]
                }

                # Create standardized detection payload
                detection = self.tracking_id_service.format_detection_payload(
                    track_id=cuid,
                    label=object_name,
                    confidence=0.8,  # Moondream doesn't provide confidence scores
                    bbox=bbox,
                    timestamp=frame_timestamp,
                    model_type=self.model_type,
                    native_id=native_id
                )

                detections.append(detection)
        
        # Visualize detections
        frame = self._visualize_moondream_detections(frame, detections)
        
        return detections, frame
    
    def _visualize_moondream_detections(self, frame: Any, detections: List[Dict[str, Any]]) -> Any:
        """Visualize Moondream detections."""
        import cv2
        h, w = frame.shape[:2]
        
        # Assign consistent colors per label
        label_to_color = {}
        color_index = 0
        
        for detection in detections:
            label = detection["label"]
            if label not in label_to_color:
                label_to_color[label] = self.colors[color_index % len(self.colors)]
                color_index += 1
        
        # Draw detections
        for detection in detections:
            label = detection["label"]
            color = label_to_color[label]
            
            bbox = detection["bbox"]
            x1 = int(bbox["x_min"] * w)
            y1 = int(bbox["y_min"] * h)
            x2 = int(bbox["x_max"] * w)
            y2 = int(bbox["y_max"] * h)
            
            # Draw rectangle
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            # Draw label (above the box if possible, else below)
            text_y = y1 - 10 if y1 - 10 > 10 else y1 + 20
            cv2.putText(frame, label, (x1, text_y), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        
        return frame
    
    def get_detection_color(self, detection: Dict[str, Any]) -> Tuple[int, int, int]:
        """Get color for detection based on label."""
        label = detection.get("label", "unknown")
        color_idx = hash(label) % len(self.colors)
        return self.colors[color_idx]
    
    def format_label_text(self, detection: Dict[str, Any], 
                         additional_info: str = "") -> str:
        """Format label text for Moondream detection."""
        label = detection.get('label', 'unknown')
        base_text = label  # Moondream doesn't provide confidence scores
        
        if additional_info:
            return f"{base_text} {additional_info}"
        return base_text