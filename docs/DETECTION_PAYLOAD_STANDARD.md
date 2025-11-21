# Detection Payload Standard

## Overview

All detection models in the Constellation Overwatch system now use a **uniform, standardized payload format** for publishing detections to NATS JetStream. This ensures consistency across data pipelines and downstream integrations.

## Standardized Payload Structure

```json
{
  "track_id": "clx7y3k2r0000qzrm8n7qh3k1",
  "model_type": "yoloe-c4isr-threat-detection",
  "label": "person",
  "confidence": 0.96,
  "bbox": {
    "x_min": 0.189,
    "y_min": 0.179,
    "x_max": 0.837,
    "y_max": 0.997
  },
  "timestamp": "2025-11-21T13:18:19.912559+00:00",
  "metadata": {
    "native_id": 1,
    "threat_level": "LOW_THREAT",
    "suspicious_indicators": []
  }
}
```

## Core Fields (Required for All Models)

| Field | Type | Description |
|-------|------|-------------|
| `track_id` | string | Globally unique CUID for the tracked object |
| `model_type` | string | Detection model identifier (e.g., `yoloe-c4isr-threat-detection`) |
| `label` | string | Object class label (e.g., `person`, `car`, `weapon`) |
| `confidence` | float | Detection confidence score (0.0 to 1.0) |
| `bbox` | object | Normalized bounding box coordinates |
| `bbox.x_min` | float | Minimum x coordinate (normalized 0-1) |
| `bbox.y_min` | float | Minimum y coordinate (normalized 0-1) |
| `bbox.x_max` | float | Maximum x coordinate (normalized 0-1) |
| `bbox.y_max` | float | Maximum y coordinate (normalized 0-1) |
| `timestamp` | string | ISO 8601 timestamp of detection |
| `metadata` | object | Model-specific metadata |

## Metadata Fields

The `metadata` object contains:
- `native_id`: The model's internal tracking ID (for debugging)
- Model-specific fields (e.g., `threat_level`, `mask`, `area`, etc.)

### Example Metadata by Model

**YOLOE C4ISR:**
```json
"metadata": {
  "native_id": 1,
  "threat_level": "HIGH_THREAT",
  "suspicious_indicators": ["high_confidence_weapon_detection"]
}
```

**SAM2 Segmentation:**
```json
"metadata": {
  "native_id": 0,
  "mask": [[0, 1, 1, ...], ...],
  "area": 2547.5
}
```

**RT-DETR:**
```json
"metadata": {
  "native_id": 2,
  "class_id": 0
}
```

## Tracking ID Service

### Purpose
- Generates **CUIDs** (Collision-resistant Unique IDs) for globally unique object identification
- Maintains mapping between model-native IDs and CUIDs
- Ensures ID persistence across frames for the same object

### Usage in Detectors

```python
# In any detector inheriting from BaseDetector

# Get or create CUID for a native tracking ID
cuid = self.tracking_id_service.get_or_create_cuid(
    native_id=yolo_track_id,
    model_type=self.model_type
)

# Create standardized payload
detection = self.tracking_id_service.format_detection_payload(
    track_id=cuid,
    label=class_name,
    confidence=float(conf),
    bbox=bbox,
    timestamp=frame_timestamp,
    model_type=self.model_type,
    native_id=yolo_track_id,
    # Model-specific fields go in metadata
    threat_level=threat_level,
    suspicious_indicators=indicators
)
```

## Benefits

1. **Globally Unique IDs**: CUIDs prevent collisions across cameras/devices
2. **Consistent Format**: Downstream systems can process all detections uniformly
3. **Model Flexibility**: Model-specific data goes in `metadata` without breaking schema
4. **Debugging Support**: `native_id` preserved for troubleshooting
5. **Type Safety**: Standardized fields enable strong typing in data pipelines

## Migration Guide

### For New Detection Models

1. Inherit from `BaseDetector`
2. Use `self.tracking_id_service` for ID generation
3. Use `format_detection_payload()` for output
4. Put model-specific fields in metadata

```python
class MyNewDetector(BaseDetector):
    def process_frame(self, frame, frame_timestamp, frame_count):
        # ... detection logic ...

        cuid = self.tracking_id_service.get_or_create_cuid(
            native_id=my_native_id,
            model_type=self.model_type
        )

        detection = self.tracking_id_service.format_detection_payload(
            track_id=cuid,
            label=label,
            confidence=confidence,
            bbox=bbox,
            timestamp=frame_timestamp,
            model_type=self.model_type,
            native_id=my_native_id,
            # Your model-specific fields
            custom_field=value
        )

        return [detection], processed_frame
```

### For Existing Models

Models needing update:
- ✅ `yoloe_c4isr.py` - **UPDATED**
- ⏳ `yoloe.py` - Pending
- ⏳ `rtdetr.py` - Pending
- ⏳ `sam2.py` - Pending
- ⏳ `moondream.py` - Pending

## NATS JetStream Integration

All detections are published to:
```
Subject: constellation.events.isr.{org_id}.{entity_id}
Stream: CONSTELLATION_EVENTS
```

Payload wrapper:
```json
{
  "timestamp": "2025-11-21T13:18:19.912559+00:00",
  "event_type": "detection",
  "entity_id": "1048bff5-5b97-4fa8-a0f1-061662b32163",
  "device_id": "b546cd5c6dc0b878",
  "detection": {
    // Standardized payload here
  }
}
```

## Smart Publishing

Detections are only published when:
1. Object first appears (new `track_id`)
2. Object moves significantly (configurable via `SIGINT_MOVEMENT_THRESHOLD`)
3. Confidence changes significantly (configurable via `SIGINT_CONFIDENCE_THRESHOLD`)
4. Label or threat level changes
5. Object disappears

This reduces event noise while maintaining data fidelity.
