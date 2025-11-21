"""
Centralized Tracking ID Service

Provides globally unique, collision-resistant identifiers (CUIDs) for all
detection models. Ensures consistent ID generation and payload formatting
across the entire detection pipeline.
"""

from typing import Dict, Any, Optional
from cuid2 import cuid_wrapper


class TrackingIDService:
    """
    Centralized service for managing tracking IDs across all detection models.

    Features:
    - Generates CUIDs (Collision-resistant Unique IDs) for global uniqueness
    - Maintains mapping between model-specific IDs and CUIDs
    - Ensures consistent payload format across all models
    - Thread-safe for multi-model deployments
    """

    def __init__(self):
        """Initialize the tracking ID service."""
        # Mapping: (model_type, native_id) -> CUID
        self.id_mapping: Dict[tuple, str] = {}

        # CUID generator
        self.cuid_generator = cuid_wrapper()

    def get_or_create_cuid(self, native_id: Any, model_type: str = "default") -> str:
        """
        Get or create a CUID for a native tracking ID.

        Args:
            native_id: The model's native tracking ID (e.g., YOLO ID, frame index)
            model_type: Type of model (yoloe, rtdetr, sam2, moondream)

        Returns:
            str: Globally unique CUID
        """
        key = (model_type, native_id)

        if key not in self.id_mapping:
            self.id_mapping[key] = self.cuid_generator()

        return self.id_mapping[key]

    def format_detection_payload(
        self,
        track_id: str,
        label: str,
        confidence: float,
        bbox: Dict[str, float],
        timestamp: str,
        model_type: str,
        native_id: Optional[Any] = None,
        **extra_fields
    ) -> Dict[str, Any]:
        """
        Format a standardized detection payload for all models.

        Args:
            track_id: The CUID tracking ID
            label: Object class label
            confidence: Detection confidence score
            bbox: Normalized bounding box {x_min, y_min, x_max, y_max}
            timestamp: ISO format timestamp
            model_type: Detection model type
            native_id: Model's native ID (for debugging)
            **extra_fields: Additional model-specific fields

        Returns:
            Standardized detection payload dictionary
        """
        payload = {
            # Core identification
            "track_id": track_id,
            "model_type": model_type,

            # Detection data
            "label": label,
            "confidence": float(confidence),
            "bbox": bbox,
            "timestamp": timestamp,

            # Metadata
            "metadata": {
                "native_id": native_id,
                **extra_fields
            }
        }

        return payload

    def cleanup_stale_ids(self, active_ids: set) -> None:
        """
        Clean up mappings for IDs that are no longer active.

        Args:
            active_ids: Set of currently active CUIDs
        """
        # Remove mappings where the CUID is not in active set
        stale_keys = [
            key for key, cuid in self.id_mapping.items()
            if cuid not in active_ids
        ]

        for key in stale_keys:
            del self.id_mapping[key]

    def get_mapping_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the current ID mappings.

        Returns:
            Dictionary with mapping statistics
        """
        model_counts = {}
        for (model_type, _), _ in self.id_mapping.items():
            model_counts[model_type] = model_counts.get(model_type, 0) + 1

        return {
            "total_mappings": len(self.id_mapping),
            "by_model": model_counts
        }
