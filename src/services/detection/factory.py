"""Factory for creating detection model instances."""

from typing import Dict, Any
from argparse import Namespace

from ...config.models import DetectionMode, get_model_config
from .base import BaseDetector

class DetectorFactory:
    """Factory for creating detector instances."""
    
    @staticmethod
    def create_detector(detection_mode: DetectionMode, args: Namespace) -> BaseDetector:
        """Create detector instance for specified mode."""
        model_config = get_model_config(detection_mode)
        
        if detection_mode == DetectionMode.YOLOE_C4ISR:
            from .yoloe_c4isr import C4ISRThreatDetector
            return C4ISRThreatDetector(args, model_config)
        
        elif detection_mode == DetectionMode.RTDETR:
            from .rtdetr import RTDETRDetector
            return RTDETRDetector(args, model_config)
        
        elif detection_mode == DetectionMode.YOLOE:
            from .yoloe import YOLOEDetector
            return YOLOEDetector(args, model_config)
        
        elif detection_mode == DetectionMode.SAM2:
            from .sam2 import SAM2Detector
            return SAM2Detector(args, model_config)
        
        elif detection_mode == DetectionMode.MOONDREAM:
            from .moondream import MoondreamDetector
            return MoondreamDetector(args, model_config)
        
        else:
            raise ValueError(f"Unknown detection mode: {detection_mode}")
    
    @staticmethod
    def get_available_modes() -> Dict[str, str]:
        """Get available detection modes and descriptions."""
        return {
            mode.value: get_model_config(mode).description
            for mode in DetectionMode
        }
    
    @staticmethod
    def list_modes() -> None:
        """Print available detection modes."""
        print("\n=== Available Detection Modes ===")
        modes = DetectorFactory.get_available_modes()
        for mode, description in modes.items():
            default_indicator = " (DEFAULT)" if mode == "yoloe_c4isr" else ""
            print(f"  {mode}{default_indicator}")
            print(f"    {description}")
        print("=" * 35)