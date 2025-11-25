# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Constellation Overwatch Object Detection Client - A real-time video edge inference system that runs object detection with various ML models and transmits detected objects as events to Constellation Overwatch via NATS JetStream and KV Store.

## Build & Run Commands

```bash
# Install dependencies (requires uv package manager)
uv sync

# Run with default model (YOLOE C4ISR threat detection)
uv run overwatch.py

# Run with specific model
uv run overwatch.py --model rtdetr      # High-speed detection
uv run overwatch.py --model yoloe       # Open-vocabulary detection
uv run overwatch.py --model sam2        # Segmentation
uv run overwatch.py --model moondream   # Vision-language model

# Video source options
uv run overwatch.py --camera 0                              # Specific camera index
uv run overwatch.py --rtsp rtsp://host:8554/live/stream     # RTSP stream
uv run overwatch.py --skip-native                           # Skip built-in cameras

# List available models and devices
uv run overwatch.py --list-models
uv run overwatch.py --list-devices

# Camera diagnostics (macOS)
uv run utils/camera_diagnostics.py
```

## Architecture

### Entry Point & Orchestration
- [src/overwatch.py](src/overwatch.py) - Main orchestrator (~290 lines), coordinates all services, runs detection loop with smart publishing

### Service Layer (`src/services/`)
- **detection/** - Detection model implementations using factory pattern
  - [base.py](src/services/detection/base.py) - `BaseDetector` abstract class with `TrackingIDService` integration
  - [factory.py](src/services/detection/factory.py) - `DetectorFactory` creates model instances by `DetectionMode` enum
  - Model implementations: `yoloe_c4isr.py` (default), `rtdetr.py`, `yoloe.py`, `sam2.py`, `moondream.py`
- **tracking/** - Object state management
  - [state.py](src/services/tracking/state.py) - `TrackingState`, `C4ISRTrackingState`, `SegmentationState` classes
  - [service.py](src/services/tracking/service.py) - `TrackingService` coordinates tracking state
- **communication/** - NATS/JetStream/KV operations
  - [service.py](src/services/communication/service.py) - `OverwatchCommunication` manages connections and publishing
  - [publisher.py](src/services/communication/publisher.py) - `ConstellationPublisher` builds message payloads
- **video/** - Video capture and display
  - [service.py](src/services/video/service.py) - `VideoService` handles camera/stream input

### Configuration (`src/config/`)
- [models.py](src/config/models.py) - `DetectionMode` enum and model configurations
- [threats.py](src/config/threats.py) - C4ISR threat classification definitions
- [defaults.py](src/config/defaults.py) - Default configuration values

### Utilities (`src/utils/`)
- [args.py](src/utils/args.py) - CLI argument parsing
- [device.py](src/utils/device.py) - Device fingerprinting
- [constellation.py](src/utils/constellation.py) - Constellation ID management
- [signals.py](src/utils/signals.py) - Signal handlers for graceful shutdown

## Key Data Flows

### Detection Pipeline
1. `VideoService` captures frames
2. `Detector.process_frame()` runs inference, returns standardized detections
3. `TrackingService` updates object state with frame persistence
4. Smart publishing filters (movement/confidence thresholds) determine what to publish
5. `OverwatchCommunication` publishes to JetStream (events) and KV store (consolidated EntityState)

### Detection Payload Standard
All models output uniform payload format - see [docs/DETECTION_PAYLOAD_STANDARD.md](docs/DETECTION_PAYLOAD_STANDARD.md):
- `track_id` - CUID for globally unique tracking
- `label`, `confidence`, `bbox` (normalized 0-1)
- `metadata` - model-specific fields (threat_level, mask, etc.)

### EntityState KV Structure
Single consolidated key per entity (`{entity_id}`) with subsignals:
- `detections` - tracked objects with bbox, confidence, threat info
- `analytics` - summary metrics
- `c4isr` - threat intelligence (C4ISR mode only)

See [docs/KV_ENTITYSTATE_SPEC.md](docs/KV_ENTITYSTATE_SPEC.md) for full schema.

## Environment Variables

Required in `.env`:
- `CONSTELLATION_ORG_ID` - Organization ID from Overwatch
- `CONSTELLATION_ENTITY_ID` - Entity ID from Overwatch

Optional:
- `SIGINT_MOVEMENT_THRESHOLD` - Movement threshold for publishing (default: 0.05 = 5%)
- `SIGINT_CONFIDENCE_THRESHOLD` - Confidence change threshold (default: 0.1 = 10%)
- `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` - Use cached models offline

## Adding New Detection Models

1. Create detector class inheriting from `BaseDetector` in `src/services/detection/`
2. Implement `load_model()` and `process_frame()` methods
3. Use `self.tracking_id_service.get_or_create_cuid()` for track IDs
4. Use `self.tracking_id_service.format_detection_payload()` for output format
5. Add `DetectionMode` enum value in `src/config/models.py`
6. Register in `DetectorFactory.create_detector()`

## NATS Integration

- Stream: `CONSTELLATION_EVENTS` on subject `constellation.events.isr.{org_id}.{entity_id}`
- KV Store: `CONSTELLATION_GLOBAL_STATE` with key `{entity_id}`
- Events: `bootsequence`, `detection`, `shutdown`
