"""Base detector class for all detection models."""

import os
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Tuple, Callable
from argparse import Namespace
from ..tracking_id import TrackingIDService


def get_models_dir() -> str:
    """Get the project's ./models directory path."""
    # Navigate from src/services/detection/ to project root
    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(script_dir, "models")


def configure_ultralytics_weights_dir(models_dir: str) -> None:
    """Configure ultralytics to download weights to the specified directory."""
    from ultralytics import settings
    settings.update({"weights_dir": models_dir})


def load_ultralytics_model(
    model_class: Callable,
    model_file: str,
    model_name: str = "model"
) -> Any:
    """
    Load an ultralytics model, downloading to ./models if needed.

    Args:
        model_class: The ultralytics model class (RTDETR, YOLOE, SAM, etc.)
        model_file: The model filename (e.g., "rtdetr-l.pt")
        model_name: Display name for logging

    Returns:
        Loaded model instance
    """
    import shutil

    models_dir = get_models_dir()
    model_path = os.path.join(models_dir, model_file)

    os.makedirs(models_dir, exist_ok=True)

    # Configure ultralytics to download weights to ./models directory
    configure_ultralytics_weights_dir(models_dir)

    # Load model if it exists in ./models
    if os.path.exists(model_path):
        print(f"Loading model from: {model_path}")
        return model_class(model_path)

    # Model doesn't exist - need to download
    print(f"Model not found at {model_path}")
    print(f"Downloading {model_name}...")

    # Load model (this triggers download)
    model = model_class(model_file)

    # Check if model was downloaded to CWD and move it to ./models/
    cwd_model_path = os.path.join(os.getcwd(), model_file)
    if os.path.exists(cwd_model_path) and cwd_model_path != model_path:
        shutil.move(cwd_model_path, model_path)
        print(f"Model moved to: {model_path}")
        # Reload from correct location
        return model_class(model_path)

    # Check other possible download locations
    alt_paths = [
        os.path.expanduser(f"~/.ultralytics/weights/{model_file}"),
        os.path.expanduser(f"~/.cache/ultralytics/weights/{model_file}"),
    ]
    for alt_path in alt_paths:
        if os.path.exists(alt_path) and alt_path != model_path:
            shutil.move(alt_path, model_path)
            print(f"Model moved to: {model_path}")
            return model_class(model_path)

    print(f"✓ Model downloaded to: {models_dir}")
    return model


class BaseDetector(ABC):
    """Abstract base class for all detection models."""

    def __init__(self, args: Namespace, model_config: Dict[str, Any]):
        self.args = args
        self.model_config = model_config
        self.model = None
        self.confidence_threshold = args.conf or model_config.confidence_threshold

        # Initialize centralized tracking ID service
        self.tracking_id_service = TrackingIDService()
        self.model_type = self.model_config.name  # e.g., "yoloe-c4isr-threat-detection"

    @abstractmethod
    async def load_model(self) -> None:
        """Load the detection model."""
        pass
    
    @abstractmethod
    def process_frame(self, frame: Any, frame_timestamp: str, 
                     frame_count: int) -> Tuple[List[Dict[str, Any]], Any]:
        """
        Process a single frame and return detections.
        
        Returns:
            Tuple of (detections_list, processed_frame)
            where detections_list contains detection dictionaries
        """
        pass
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get model information for fingerprinting."""
        return self.model_config.to_fingerprint_data()
    
    def should_skip_small_detections(self, detection: Dict[str, Any]) -> bool:
        """Check if detection should be skipped (e.g., too small)."""
        return False  # Override in subclasses as needed
    
    def get_detection_color(self, detection: Dict[str, Any]) -> Tuple[int, int, int]:
        """Get color for detection visualization."""
        return (0, 255, 0)  # Default green
    
    def format_label_text(self, detection: Dict[str, Any], 
                         additional_info: str = "") -> str:
        """Format label text for detection."""
        label = detection.get('label', 'unknown')
        confidence = detection.get('confidence', 0.0)
        base_text = f"{label} {confidence:.2f}"
        
        if additional_info:
            return f"{base_text} {additional_info}"
        return base_text
    
    def draw_detection_box(self, frame: Any, detection: Dict[str, Any], 
                          color: Tuple[int, int, int], thickness: int = 2) -> None:
        """Draw bounding box for detection."""
        import cv2
        h, w = frame.shape[:2]
        
        bbox = detection['bbox']
        x1 = int(bbox['x_min'] * w)
        y1 = int(bbox['y_min'] * h)
        x2 = int(bbox['x_max'] * w)
        y2 = int(bbox['y_max'] * h)
        
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
        
        return x1, y1, x2, y2
    
    def draw_detection_label(self, frame: Any, label_text: str, 
                           x1: int, y1: int, color: Tuple[int, int, int]) -> None:
        """Draw label for detection."""
        import cv2
        
        # Calculate text position
        text_y = y1 - 10 if y1 - 10 > 10 else y1 + 20
        
        # Draw text
        cv2.putText(frame, label_text, (x1, text_y),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    
    def visualize_detections(self, frame: Any, detections: List[Dict[str, Any]], 
                           additional_info_fn=None) -> Any:
        """Visualize detections on frame."""
        for detection in detections:
            color = self.get_detection_color(detection)
            
            # Draw bounding box
            x1, y1, x2, y2 = self.draw_detection_box(frame, detection, color)
            
            # Get additional info if function provided
            additional_info = ""
            if additional_info_fn:
                additional_info = additional_info_fn(detection)
            
            # Draw label
            label_text = self.format_label_text(detection, additional_info)
            self.draw_detection_label(frame, label_text, x1, y1, color)
        
        return frame
    
    def add_status_overlay(self, frame: Any, device_id: str, 
                          stats: Dict[str, Any]) -> Any:
        """Add status overlay to frame."""
        import cv2
        
        # Status line 1: Device and basic stats
        status_text = f"Device: {device_id[:8]} | {self.model_config.description}"
        cv2.putText(frame, status_text, (10, 30), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        
        # Status line 2: Detection stats
        if 'active_count' in stats:
            stats_text = f"Active: {stats.get('active_count', 0)} | Total: {stats.get('total_unique', 0)}"
            cv2.putText(frame, stats_text, (10, 55), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        
        return frame