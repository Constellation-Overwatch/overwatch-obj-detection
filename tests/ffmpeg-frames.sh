#!/bin/bash

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Check required environment variables
if [ -z "$CONSTELLATION_ENTITY_ID" ]; then
    echo "Error: CONSTELLATION_ENTITY_ID environment variable is required"
    exit 1
fi

# Use environment variables for NATS configuration
NATS_URL=${NATS_URL:-"nats://localhost:4222"}
VIDEO_SUBJECT_ROOT=${NATS_VIDEO_SUBJECT_ROOT:-"constellation.video"}

# Build NATS server URL with optional auth token
if [ -n "$NATS_AUTH_TOKEN" ]; then
    NATS_PROTOCOL="${NATS_URL%%://*}"
    NATS_HOST="${NATS_URL#*://}"
    NATS_SERVER="${NATS_PROTOCOL}://${NATS_AUTH_TOKEN}@${NATS_HOST}"
else
    NATS_SERVER="$NATS_URL"
fi

SUBJECT="${VIDEO_SUBJECT_ROOT}.${CONSTELLATION_ENTITY_ID}"
FRAME_DIR=$(mktemp -d)
FRAME_RATE=${FRAME_RATE:-5}

cleanup() {
    echo ""
    echo "Cleaning up..."
    kill $FFMPEG_PID 2>/dev/null
    rm -rf "$FRAME_DIR"
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "Publishing video frames to: $SUBJECT"
echo "NATS server: ${NATS_URL} (auth: $([ -n "$NATS_AUTH_TOKEN" ] && echo 'yes' || echo 'no'))"
echo "Frame rate: ${FRAME_RATE} fps"
echo "Temp dir: $FRAME_DIR"
echo "Press Ctrl+C to stop"
echo ""

# Start ffmpeg writing individual JPEG frames to temp directory
ffmpeg -f avfoundation -framerate 30 -i "0" \
  -vf "fps=${FRAME_RATE}" -q:v 5 \
  -f image2 -strftime 0 "${FRAME_DIR}/frame_%06d.jpg" \
  2>/dev/null &
FFMPEG_PID=$!

# Give ffmpeg a moment to start
sleep 1

# Check ffmpeg started successfully
if ! kill -0 $FFMPEG_PID 2>/dev/null; then
    echo "Error: ffmpeg failed to start"
    rm -rf "$FRAME_DIR"
    exit 1
fi

echo "Streaming..."

# Publish frames as they appear
frame_count=0
while kill -0 $FFMPEG_PID 2>/dev/null; do
    for frame in "$FRAME_DIR"/frame_*.jpg; do
        [ -f "$frame" ] || continue

        # Wait for frame to be fully written
        size1=$(stat -f%z "$frame" 2>/dev/null || echo 0)
        sleep 0.02
        size2=$(stat -f%z "$frame" 2>/dev/null || echo 0)
        [ "$size1" != "$size2" ] && continue
        [ "$size1" -eq 0 ] && continue

        # Publish and remove
        if nats pub "$SUBJECT" --server "$NATS_SERVER" < "$frame" 2>/dev/null; then
            frame_count=$((frame_count + 1))
            printf "\rFrames published: %d (last: %d bytes)" "$frame_count" "$size1"
        fi
        rm -f "$frame"
    done
    sleep 0.05
done

echo ""
echo "Total frames published: $frame_count"
cleanup
