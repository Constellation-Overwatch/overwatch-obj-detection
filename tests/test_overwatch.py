#!/usr/bin/env python3
"""
Test script for Overwatch detection system.
Tests all detection models and basic functionality.
"""

import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_imports():
    """Test that all imports work correctly."""
    print("Testing imports...")
    
    try:
        from src.config.models import DetectionMode, get_model_config
        from src.config.threats import THREAT_CATEGORIES, ALL_CLASSES
        from src.config.defaults import DEFAULT_CONFIG
        print("✓ Config imports successful")
        
        from src.utils.args import parse_arguments
        from src.utils.device import enumerate_video_devices
        print("✓ Utils imports successful")
        
        from src.services.detection.factory import DetectorFactory
        from src.services.tracking.service import TrackingService
        from src.services.communication.service import OverwatchCommunication
        from src.services.video.service import VideoService
        print("✓ Services imports successful")
        
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False

def test_detection_modes():
    """Test detection mode configuration."""
    print("\nTesting detection modes...")
    
    try:
        from src.config.models import DetectionMode, get_model_config
        
        for mode in DetectionMode:
            config = get_model_config(mode)
            print(f"  {mode.value}: {config.description}")
        
        print("✓ All detection modes configured")
        return True
    except Exception as e:
        print(f"✗ Detection mode test failed: {e}")
        return False

def test_c4isr_threats():
    """Test C4ISR threat configuration."""
    print("\nTesting C4ISR threat configuration...")
    
    try:
        from src.config.threats import THREAT_CATEGORIES, ALL_CLASSES, CLASS_TO_THREAT_LEVEL
        
        total_classes = 0
        for threat_level, config in THREAT_CATEGORIES.items():
            class_count = len(config['classes'])
            total_classes += class_count
            print(f"  {threat_level}: {class_count} classes")
        
        print(f"  Total threat classes: {len(ALL_CLASSES)} (expected: {total_classes})")
        print(f"  Class mappings: {len(CLASS_TO_THREAT_LEVEL)}")
        
        # Test a few specific mappings
        assert CLASS_TO_THREAT_LEVEL.get('weapon') == 'HIGH_THREAT'
        assert CLASS_TO_THREAT_LEVEL.get('person') == 'LOW_THREAT'
        
        print("✓ C4ISR threat configuration valid")
        return True
    except Exception as e:
        print(f"✗ C4ISR threat test failed: {e}")
        return False

def test_factory():
    """Test detector factory."""
    print("\nTesting detector factory...")
    
    try:
        from src.services.detection.factory import DetectorFactory
        from src.config.models import DetectionMode
        from argparse import Namespace
        
        # Test factory methods
        modes = DetectorFactory.get_available_modes()
        print(f"  Available modes: {list(modes.keys())}")
        
        # Test creating a detector (don't load model)
        args = Namespace(
            model='yoloe_c4isr',
            conf=0.25,
            custom_threats=None,
            tracker='botsort.yaml',
            imgsz=1024,
            prompt='Objects',
            max_objects=50,
            min_frames=1
        )
        
        detector = DetectorFactory.create_detector(DetectionMode.YOLOE_C4ISR, args)
        assert detector is not None
        print(f"  Created detector: {detector.__class__.__name__}")
        
        print("✓ Detector factory works")
        return True
    except Exception as e:
        print(f"✗ Factory test failed: {e}")
        return False

def test_video_enumeration():
    """Test video device enumeration."""
    print("\nTesting video device enumeration...")
    
    try:
        from src.utils.device import enumerate_video_devices
        
        devices = enumerate_video_devices()
        print(f"  Found {len(devices)} video devices")
        
        for i, device in enumerate(devices[:3]):  # Show first 3
            print(f"    {i+1}. {device.get('name', 'Unknown')} ({device.get('type', 'unknown')})")
        
        print("✓ Video enumeration works")
        return True
    except Exception as e:
        print(f"✗ Video enumeration test failed: {e}")
        return False

def test_argument_parsing():
    """Test argument parsing."""
    print("\nTesting argument parsing...")
    
    try:
        from src.utils.args import parse_arguments, validate_arguments
        import sys
        
        # Save original args
        original_argv = sys.argv.copy()
        
        # Test default arguments
        sys.argv = ['test']
        args = validate_arguments(parse_arguments())
        assert args.model == 'yoloe_c4isr'  # Default
        print("  ✓ Default arguments")
        
        # Test model selection
        sys.argv = ['test', '--model', 'rtdetr']
        args = validate_arguments(parse_arguments())
        assert args.model == 'rtdetr'
        print("  ✓ Model selection")
        
        # Test camera arguments
        sys.argv = ['test', '--camera', '1', '--conf', '0.3']
        args = validate_arguments(parse_arguments())
        assert args.camera == 1
        assert args.conf == 0.3
        print("  ✓ Camera arguments")
        
        # Restore original args
        sys.argv = original_argv
        
        print("✓ Argument parsing works")
        return True
    except Exception as e:
        print(f"✗ Argument parsing test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("=" * 50)
    print("OVERWATCH DETECTION SYSTEM - TEST SUITE")
    print("=" * 50)
    
    tests = [
        test_imports,
        test_detection_modes,
        test_c4isr_threats,
        test_factory,
        test_video_enumeration,
        test_argument_parsing
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        else:
            break  # Stop on first failure
    
    print(f"\n{'=' * 50}")
    print(f"TESTS COMPLETED: {passed}/{total} PASSED")
    
    if passed == total:
        print("🎉 ALL TESTS PASSED! The modular system is ready.")
        print("\nUsage examples:")
        print("  python overwatch.py                    # Default C4ISR mode") 
        print("  python overwatch.py --list-models      # List detection modes")
        print("  python overwatch.py --list-devices     # List video devices")
        print("  python overwatch.py --model rtdetr     # RT-DETR detection")
        print("  python overwatch.py --model yoloe      # YOLOE tracking")
        print("  python overwatch.py --model sam2       # SAM2 segmentation")
    else:
        print("❌ Some tests failed. Please fix issues before using.")
        sys.exit(1)
    
    print("=" * 50)

if __name__ == "__main__":
    main()