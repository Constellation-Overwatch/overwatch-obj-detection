# Constellation Overwatch Object Detection Client

Run video edge inference with [Moondream](https://moondream.ai) and transmit detected objects as events to [Constellation Overwatch](https://github.com/Constellation-Overwatch/constellation-overwatch).

### EXPERIMENTAL
Only tested on macOS. Needs to be integrated with ffmpeg streams from ROS and aviation feeds.

## Requirements

- [Constellation Overwatch](https://github.com/Constellation-Overwatch/constellation-overwatch)
- [uv](https://astral.sh) - A fast Python package installer and resolver

## Data Flow Diagram

```mermaid
sequenceDiagram
    participant Client as Object Detection Client
    participant Camera as Webcam
    participant Model as Moondream Model
    participant NATS as NATS Server
    participant JS as JetStream
    participant Overwatch as Constellation Overwatch

    Note over Client: Initialization Phase
    Client->>Client: Generate Device Fingerprint<br/>(hostname, MAC, platform, etc.)
    Client->>NATS: Connect to nats://localhost:4222
    NATS-->>Client: Connection established
    Client->>JS: Create JetStream context
    JS-->>Client: Stream: CONSTELLATION_EVENTS
    Client->>JS: Publish bootsequence event<br/>(device metadata + fingerprint)
    JS-->>Overwatch: Event stored & forwarded

    Note over Client: Model Loading
    Client->>Model: Load vikhyatk/moondream2
    Model-->>Client: Model ready (bfloat16, MPS/CUDA)

    Note over Client: Detection Loop
    loop Video Stream Processing
        Client->>Camera: Capture frame
        Camera-->>Client: BGR frame data
        Client->>Client: Convert BGR to RGB (PIL Image)

        Note over Client,Model: Object Detection
        Client->>Model: Query: "List all objects"
        Model-->>Client: Comma-separated object list

        loop For each detected object
            Client->>Model: Detect bounding boxes
            Model-->>Client: {x_min, y_min, x_max, y_max}
        end

        alt Objects detected
            Client->>Client: Create detection event<br/>(timestamp, objects, device_id)
            Client->>JS: Publish to constellation.events.isr
            JS-->>Overwatch: Detection event stored & forwarded
            Note over JS,Overwatch: Event includes:<br/>- Detections array<br/>- Device fingerprint<br/>- Timestamp (UTC)
        end

        Client->>Client: Draw bounding boxes on frame
        Client->>Client: Display annotated video feed
    end

    Note over Client: Shutdown Phase
    Client->>JS: Publish shutdown event
    JS-->>Overwatch: Shutdown notification
    Client->>Camera: Release resources
    Client->>NATS: Drain & close connection
```

## Installation & Setup

```sh
git clone https://github.com/Constellation-Overwatch/overwatch-obj-detection.git
cd overwatch-obj-detection
uv sync
```

### Environment Configuration

The client requires Constellation identifiers to connect to Overwatch. These can be provided via environment variables using a `.env` file:

1. Copy the example environment file:

```sh
cp .env.example .env
```

2. Edit `.env` and add your Constellation credentials:

```env
CONSTELLATION_ORG_ID=your-organization-id-here
CONSTELLATION_ENTITY_ID=your-entity-id-here
```

You can obtain these IDs from:

- Constellation Overwatch Edge Awareness Kit UI
- Your Database Administrator

If these environment variables are not set, the client will prompt you to enter them interactively at runtime.

## Usage

### Default Webcam Mode

```sh
uv run detect.py
```

### RTSP Stream Mode

To connect to an RTSP stream (e.g., from FFmpeg):

```sh
uv run detect.py --rtsp-ip 192.168.50.2 --rtsp-port 8554 --rtsp-path /live/stream
```

Command line options:

- `--rtsp-ip`: RTSP stream IP address
- `--rtsp-port`: RTSP stream port (default: 8554)
- `--rtsp-path`: RTSP stream path (default: /live/stream)
