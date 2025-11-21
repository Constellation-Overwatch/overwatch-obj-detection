"""C4ISR threat classification configuration."""

from typing import Dict, List, Tuple

# C4ISR THREAT CLASSIFICATION CONFIGURATION
THREAT_CATEGORIES = {
    "HIGH_THREAT": {
        "classes": ["weapon", "knife", "gun", "rifle", "pistol", "explosive", "bomb"],
        "color": (0, 0, 255),  # Red
        "priority": 1,
        "alert_level": "CRITICAL"
    },
    "MEDIUM_THREAT": {
        "classes": [
            "suspicious package", "unattended bag", "backpack", "suitcase",
            "unauthorized vehicle", "truck", "van"
        ],
        "color": (0, 165, 255),  # Orange  
        "priority": 2,
        "alert_level": "WARNING"
    },
    "LOW_THREAT": {
        "classes": ["person", "car", "bicycle", "motorcycle", "dog"],
        "color": (0, 255, 255),  # Yellow
        "priority": 3,
        "alert_level": "MONITOR" 
    },
    "NORMAL": {
        "classes": ["traffic light", "stop sign", "bench", "bird", "cat"],
        "color": (0, 255, 0),  # Green
        "priority": 4,
        "alert_level": "NORMAL"
    }
}

# Build comprehensive class list for detection
def build_class_mappings() -> Tuple[List[str], Dict[str, str]]:
    """Build class list and threat level mappings."""
    all_classes = []
    class_to_threat_level = {}
    
    for threat_level, config in THREAT_CATEGORIES.items():
        for cls in config["classes"]:
            all_classes.append(cls)
            class_to_threat_level[cls] = threat_level
    
    return all_classes, class_to_threat_level

# Pre-built mappings
ALL_CLASSES, CLASS_TO_THREAT_LEVEL = build_class_mappings()

def get_threat_level(class_name: str) -> str:
    """Get threat level for a detected class."""
    return CLASS_TO_THREAT_LEVEL.get(class_name, "NORMAL")

def get_threat_color(threat_level: str) -> Tuple[int, int, int]:
    """Get color for threat level."""
    return THREAT_CATEGORIES.get(threat_level, THREAT_CATEGORIES["NORMAL"])["color"]

def add_custom_threat_class(class_name: str, threat_level: str = "MEDIUM_THREAT") -> None:
    """Add custom threat class dynamically."""
    if class_name not in ALL_CLASSES:
        ALL_CLASSES.append(class_name)
        CLASS_TO_THREAT_LEVEL[class_name] = threat_level