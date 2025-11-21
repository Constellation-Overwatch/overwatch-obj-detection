#!/usr/bin/env python3
"""
Constellation Overwatch ISR Detection System

Main entry point that launches the modular detection system.
Supports all original flags and functionality while providing a clean, 
maintainable architecture.

Usage:
    # Default C4ISR threat detection
    python overwatch.py
    
    # Specific models
    python overwatch.py --model rtdetr
    python overwatch.py --model yoloe --tracker bytetrack.yaml
    python overwatch.py --model sam2 --imgsz 1024
    
    # All original camera flags work
    python overwatch.py --camera 1 --conf 0.3 --skip-native
    python overwatch.py --rtsp rtsp://192.168.1.100:8554/live/stream
"""

import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.overwatch import main

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())