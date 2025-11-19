#!/usr/bin/env python3
"""
Camera Diagnostics Tool for macOS OpenCV Issues

This script helps diagnose and verify camera selection issues on macOS
when using OpenCV with multiple cameras.

Issues addressed:
- Wrong camera opened despite specifying correct index
- Unstable camera indices across runs
- Inability to verify which camera is actually opened
"""

import cv2
import json
import subprocess
import platform
import sys


def get_avfoundation_cameras():
    """Get camera information from macOS system_profiler"""
    cameras = {}

    if platform.system() != 'Darwin':
        print("Warning: This function is macOS-specific")
        return cameras

    try:
        result = subprocess.run(
            ['system_profiler', 'SPCameraDataType', '-json'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            if 'SPCameraDataType' in data:
                for idx, cam in enumerate(data['SPCameraDataType']):
                    cameras[idx] = {
                        'name': cam.get('_name', f'Camera {idx}'),
                        'model_id': cam.get('model_id', 'unknown'),
                        'unique_id': cam.get('unique_id', 'unknown'),
                        'system_index': idx
                    }
    except Exception as e:
        print(f"Error getting camera info from system_profiler: {e}")

    return cameras


def enumerate_opencv_cameras(max_index=5, backend=cv2.CAP_AVFOUNDATION):
    """Enumerate cameras visible to OpenCV"""
    devices = []

    print(f"\n{'='*70}")
    print(f"Enumerating OpenCV cameras (indices 0-{max_index-1})")
    print(f"Backend: {get_backend_name(backend)}")
    print(f"{'='*70}")

    for index in range(max_index):
        print(f"\nTesting index {index}...")
        cap = cv2.VideoCapture(index, backend)

        if cap.isOpened():
            # Get camera properties
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = int(cap.get(cv2.CAP_PROP_FPS))
            backend_name = cap.getBackendName()

            # Try to read a frame to verify camera works
            ret, frame = cap.read()
            can_read = ret and frame is not None

            device_info = {
                'opencv_index': index,
                'resolution': f"{width}x{height}",
                'fps': fps,
                'backend': backend_name,
                'can_read_frame': can_read
            }

            devices.append(device_info)

            print(f"  ✓ Camera found at index {index}")
            print(f"    Resolution: {width}x{height}")
            print(f"    FPS: {fps}")
            print(f"    Backend: {backend_name}")
            print(f"    Can read frame: {can_read}")

            cap.release()
        else:
            print(f"  ✗ No camera at index {index}")

    return devices


def get_backend_name(backend_id):
    """Convert backend ID to readable name"""
    backends = {
        cv2.CAP_AVFOUNDATION: "AVFoundation (macOS)",
        cv2.CAP_ANY: "Auto-detect",
        cv2.CAP_V4L2: "V4L2 (Linux)",
        cv2.CAP_DSHOW: "DirectShow (Windows)"
    }
    return backends.get(backend_id, f"Unknown ({backend_id})")


def test_camera_with_preview(index, backend=cv2.CAP_AVFOUNDATION, duration_sec=3):
    """
    Open a camera and display preview to verify which physical camera is opened

    Args:
        index: OpenCV camera index
        backend: Video backend to use
        duration_sec: How long to show preview (seconds)
    """
    print(f"\n{'='*70}")
    print(f"Testing Camera Index {index}")
    print(f"{'='*70}")

    cap = cv2.VideoCapture(index, backend)

    if not cap.isOpened():
        print(f"ERROR: Could not open camera at index {index}")
        return False

    # Get properties
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    backend_name = cap.getBackendName()

    print(f"Camera opened successfully:")
    print(f"  Index: {index}")
    print(f"  Backend: {backend_name}")
    print(f"  Resolution: {width}x{height}")
    print(f"  FPS: {fps}")
    print(f"\nShowing preview for {duration_sec} seconds...")
    print(f"Wave at the camera to verify which one is active!")
    print(f"Press 'q' to exit early.")

    window_name = f'Camera Index {index} - Verify Physical Camera'
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 640, 480)

    import time
    start_time = time.time()
    frame_count = 0

    try:
        while True:
            ret, frame = cap.read()

            if not ret:
                print("ERROR: Failed to read frame")
                break

            frame_count += 1
            elapsed = time.time() - start_time

            # Add overlay with info
            text = f"Index {index} | {width}x{height} | Frame {frame_count}"
            cv2.putText(frame, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            instruction = "Wave at camera to verify! Press 'q' to exit"
            cv2.putText(frame, instruction, (10, height - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

            cv2.imshow(window_name, frame)

            # Exit if duration elapsed or 'q' pressed
            if cv2.waitKey(1) & 0xFF == ord('q') or elapsed >= duration_sec:
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print(f"\nCaptured {frame_count} frames in {elapsed:.2f} seconds")

    return True


def run_diagnostics():
    """Run comprehensive camera diagnostics"""
    print("\n" + "="*70)
    print("OpenCV Camera Diagnostics for macOS")
    print("="*70)
    print(f"OpenCV Version: {cv2.__version__}")
    print(f"Platform: {platform.system()} {platform.release()}")

    # Get system camera information
    print("\n" + "="*70)
    print("System Cameras (from system_profiler)")
    print("="*70)

    system_cameras = get_avfoundation_cameras()
    if system_cameras:
        for idx, cam_info in system_cameras.items():
            print(f"\nCamera {idx}:")
            print(f"  Name: {cam_info['name']}")
            print(f"  Model ID: {cam_info['model_id']}")
            print(f"  Unique ID: {cam_info['unique_id']}")
    else:
        print("No cameras found via system_profiler")

    # Enumerate OpenCV cameras
    opencv_cameras = enumerate_opencv_cameras(max_index=5, backend=cv2.CAP_AVFOUNDATION)

    # Summary
    print("\n" + "="*70)
    print("Summary")
    print("="*70)
    print(f"System cameras found: {len(system_cameras)}")
    print(f"OpenCV cameras found: {len(opencv_cameras)}")

    if len(system_cameras) != len(opencv_cameras):
        print("\n⚠️  WARNING: Mismatch between system cameras and OpenCV cameras!")
        print("This may indicate camera access issues.")

    # Offer to test cameras with preview
    if opencv_cameras:
        print("\n" + "="*70)
        print("Camera Preview Test")
        print("="*70)
        print("\nWould you like to preview each camera to verify which is which?")
        response = input("Enter 'y' for yes, or any other key to skip: ").strip().lower()

        if response == 'y':
            for cam in opencv_cameras:
                test_camera_with_preview(cam['opencv_index'], cv2.CAP_AVFOUNDATION, duration_sec=3)
                print()


if __name__ == "__main__":
    try:
        run_diagnostics()
    except KeyboardInterrupt:
        print("\n\nDiagnostics interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
