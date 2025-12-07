"""H.264 video encoder for WebRTC-compatible streaming via FFmpeg.

Outputs MPEG-TS format optimized for WebRTC ingestion:
- libx264 codec with baseline profile (browser compatible)
- ultrafast preset + zerolatency tune (real-time control)
- MPEG-TS container (what WebRTCHandler expects)

Bandwidth: ~0.15-0.3 MB/s vs ~1.5-2.5 MB/s for MJPEG (~90% savings)
"""

import subprocess
import threading
import queue
import numpy as np
from typing import Dict, Any, Tuple, Optional
import time


class H264Encoder:
    """
    Streaming H.264 encoder using FFmpeg subprocess pipe.

    Publishes MPEG-TS chunks continuously for real-time streaming.
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        fps: int = 15,
        bitrate: str = "1500k",
        gop_size: int = 30,
    ):
        self.width = width
        self.height = height
        self.fps = fps
        self.bitrate = bitrate
        self.gop_size = gop_size

        self._process: Optional[subprocess.Popen] = None
        self._output_queue: queue.Queue = queue.Queue(maxsize=60)
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._frame_count = 0
        self._bytes_encoded = 0
        self._start_time: Optional[float] = None
        self._input_resolution: Tuple[int, int] = (0, 0)

    def start(self, input_width: int, input_height: int) -> bool:
        """Start FFmpeg encoder process."""
        if self._running:
            return True

        self._input_resolution = (input_width, input_height)

        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            # Input: raw BGR frames from OpenCV via stdin
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{input_width}x{input_height}",
            "-r", str(self.fps),
            "-i", "-",
            # H.264 encoding (WebRTC optimized)
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-profile:v", "baseline",
            "-pix_fmt", "yuv420p",
            "-b:v", self.bitrate,
            "-g", str(self.gop_size),
            # Output: MPEG-TS to stdout
            "-f", "mpegts",
            "-",
        ]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            self._running = True
            self._start_time = time.monotonic()
            self._frame_count = 0
            self._bytes_encoded = 0

            # Background thread reads MPEG-TS output
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()

            return True

        except FileNotFoundError:
            print("Error: FFmpeg not found. Install with: brew install ffmpeg")
            return False
        except Exception as e:
            print(f"Error starting H.264 encoder: {e}")
            return False

    def _read_output(self) -> None:
        """Read MPEG-TS chunks from FFmpeg stdout."""
        # MPEG-TS packet size is 188 bytes
        # Optimal: 7 * 188 = 1316 bytes (fits in single UDP datagram)
        # Higher throughput: 20 * 188 = 3760 bytes
        chunk_size = 188 * 7  # UDP-friendly chunk size

        while self._running and self._process:
            try:
                chunk = self._process.stdout.read(chunk_size)
                if chunk:
                    self._bytes_encoded += len(chunk)
                    try:
                        self._output_queue.put_nowait(chunk)
                    except queue.Full:
                        # Drop oldest chunk if queue full (real-time priority)
                        try:
                            self._output_queue.get_nowait()
                            self._output_queue.put_nowait(chunk)
                        except queue.Empty:
                            pass
                elif self._process.poll() is not None:
                    break
            except Exception:
                break

    def encode_frame(self, frame: np.ndarray) -> Tuple[Optional[bytes], Dict[str, Any]]:
        """
        Write frame to encoder, return any available MPEG-TS output.

        Args:
            frame: BGR numpy array from OpenCV

        Returns:
            (mpegts_bytes or None, metadata_dict)
        """
        h, w = frame.shape[:2]

        # Start encoder on first frame or if resolution changed
        if not self._running or (w, h) != self._input_resolution:
            if self._running:
                self.stop()
            if not self.start(w, h):
                return None, {"error": "Failed to start encoder"}

        try:
            # Feed frame to FFmpeg
            self._process.stdin.write(frame.tobytes())
            self._process.stdin.flush()
            self._frame_count += 1

            # Collect available MPEG-TS output
            chunks = []
            while True:
                try:
                    chunks.append(self._output_queue.get_nowait())
                except queue.Empty:
                    break

            mpegts_bytes = b"".join(chunks) if chunks else None

            metadata = {
                "width": self.width,
                "height": self.height,
                "original_width": w,
                "original_height": h,
                "format": "h264",
                "container": "mpegts",
                "profile": "baseline",
                "preset": "ultrafast",
                "bitrate": self.bitrate,
                "fps": self.fps,
                "frame_number": self._frame_count,
                "size_bytes": len(mpegts_bytes) if mpegts_bytes else 0,
            }

            return mpegts_bytes, metadata

        except BrokenPipeError:
            self._running = False
            return None, {"error": "Encoder pipe broken"}
        except Exception as e:
            return None, {"error": str(e)}

    def get_stats(self) -> Dict[str, Any]:
        """Get encoder statistics."""
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        return {
            "frames_encoded": self._frame_count,
            "bytes_encoded": self._bytes_encoded,
            "elapsed_seconds": elapsed,
            "avg_fps": self._frame_count / elapsed if elapsed > 0 else 0,
            "avg_bitrate_kbps": (self._bytes_encoded * 8 / 1000) / elapsed if elapsed > 0 else 0,
            "resolution": f"{self.width}x{self.height}",
            "running": self._running,
        }

    def stop(self) -> None:
        """Stop encoder process."""
        self._running = False

        if self._process:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
            except Exception:
                pass
            self._process = None

        # Drain queue
        while not self._output_queue.empty():
            try:
                self._output_queue.get_nowait()
            except queue.Empty:
                break

    def __del__(self):
        self.stop()
