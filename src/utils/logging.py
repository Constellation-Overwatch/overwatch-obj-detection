"""Logging utilities."""

import os
import cv2
from ..config.defaults import setup_opencv_environment

def setup_logging():
    """Setup logging configuration for the application."""
    # Setup OpenCV environment to suppress logging
    setup_opencv_environment()
    
    # Set OpenCV log level
    cv2.setLogLevel(0)
    
    print("Logging configured: OpenCV messages suppressed")

def enable_verbose_logging():
    """Enable verbose logging for debugging."""
    cv2.setLogLevel(3)  # Show errors
    print("Verbose logging enabled")