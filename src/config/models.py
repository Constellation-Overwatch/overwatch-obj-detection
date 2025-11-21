"""Model configuration and detection modes."""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Optional

class DetectionMode(Enum):
    """Available detection modes."""
    YOLOE_C4ISR = "yoloe_c4isr"    # Default - C4ISR threat detection
    RTDETR = "rtdetr"              # RT-DETR object detection  
    YOLOE = "yoloe"                # YOLOE object tracking
    SAM2 = "sam2"                  # SAM2 segmentation
    MOONDREAM = "moondream"        # Moondream text-based detection

@dataclass
class ModelConfig:
    """Configuration for detection models."""
    name: str
    model_file: str
    confidence_threshold: float = 0.25
    description: str = ""
    requires_prompts: bool = False
    supports_tracking: bool = False
    supports_segmentation: bool = False
    component_type: str = "object-detection"
    
    def to_fingerprint_data(self) -> Dict[str, Any]:
        """Convert to device fingerprint format."""
        return {
            'name': 'constellation-isr',
            'type': self.component_type,
            'version': '1.0.0',
            'model': self.model_file,
            'mode': self.name,
            'description': self.description
        }

# Model configurations
MODEL_CONFIGS = {
    DetectionMode.YOLOE_C4ISR: ModelConfig(
        name="yoloe-c4isr-threat-detection",
        model_file="yoloe-11l-seg.pt",
        component_type="yoloe-c4isr-threat-detection",
        description="YOLOE with C4ISR threat classification and object tracking",
        supports_tracking=False,  # Uses frame-based detection for immediate threats
        confidence_threshold=0.25
    ),
    DetectionMode.RTDETR: ModelConfig(
        name="rtdetr-object-detection", 
        model_file="rtdetr-l.pt",
        component_type="rtdetr-object-detection",
        description="RT-DETR real-time object detection",
        confidence_threshold=0.25
    ),
    DetectionMode.YOLOE: ModelConfig(
        name="yoloe-object-tracking",
        model_file="yoloe-11l-seg.pt", 
        component_type="yoloe-object-tracking",
        description="YOLOE with object tracking (BoT-SORT/ByteTrack)",
        supports_tracking=True,
        confidence_threshold=0.25
    ),
    DetectionMode.SAM2: ModelConfig(
        name="sam-segmentation",
        model_file="sam2_b.pt",
        component_type="sam-segmentation", 
        description="SAM2 automatic mask generation segmentation",
        supports_segmentation=True,
        confidence_threshold=0.25
    ),
    DetectionMode.MOONDREAM: ModelConfig(
        name="moondream-object-detection",
        model_file="vikhyatk/moondream2",
        component_type="moondream-object-detection",
        description="Moondream2 text-based object detection", 
        requires_prompts=True,
        confidence_threshold=0.5
    )
}

def get_model_config(mode: DetectionMode) -> ModelConfig:
    """Get configuration for detection mode."""
    return MODEL_CONFIGS[mode]

def get_default_mode() -> DetectionMode:
    """Get default detection mode (C4ISR)."""
    return DetectionMode.YOLOE_C4ISR