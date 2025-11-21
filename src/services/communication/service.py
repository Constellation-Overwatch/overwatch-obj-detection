"""Communication service for NATS/JetStream and KV store operations."""

import json
import nats
from nats.js.api import KeyValueConfig
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from ...config.defaults import DEFAULT_CONFIG
from ...utils.constellation import get_constellation_ids
from .publisher import ConstellationPublisher, build_kv_key

class OverwatchCommunication:
    """Service for managing NATS/JetStream communications."""
    
    def __init__(self):
        self.nc: Optional[nats.NATS] = None
        self.js = None
        self.kv = None
        self.organization_id: Optional[str] = None
        self.entity_id: Optional[str] = None
        self.subject: Optional[str] = None
        self.stream_name: Optional[str] = None
        self.device_fingerprint: Optional[Dict] = None
        self.publisher: Optional[ConstellationPublisher] = None

        # Configuration
        self.nats_config = DEFAULT_CONFIG["nats"]
    
    async def initialize(self, device_fingerprint: Dict[str, Any]) -> None:
        """Initialize NATS connection and setup streams."""
        self.device_fingerprint = device_fingerprint
        
        # Get constellation identifiers
        self.organization_id, self.entity_id = get_constellation_ids()

        # Initialize publisher abstraction with identity context
        self.publisher = ConstellationPublisher(
            organization_id=self.organization_id,
            entity_id=self.entity_id,
            device_id=device_fingerprint['device_id']
        )

        # Construct subject and stream names
        self.subject = f"{self.nats_config['subject_root']}.{self.organization_id}.{self.entity_id}"
        self.stream_name = self.nats_config["stream_name"]
        
        print(f"Configured NATS subject: {self.subject}")
        print(f"Configured stream name: {self.stream_name}")
        print(f"Configured KV store: {self.nats_config['kv_store_name']}\n")
        
        # Connect to NATS
        await self._connect_nats()
        await self._setup_jetstream()
        await self._setup_kv_store()
        await self._publish_bootsequence()
    
    async def _connect_nats(self) -> None:
        """Connect to NATS server."""
        self.nc = await nats.connect(self.nats_config["url"])
        print("Connected to NATS server")
    
    async def _setup_jetstream(self) -> None:
        """Setup JetStream context."""
        self.js = self.nc.jetstream()
        
        # Verify stream exists
        try:
            stream_info = await self.js.stream_info(self.stream_name)
            print(f"Connected to JetStream stream: {self.stream_name}")
            print(f"Stream subjects: {stream_info.config.subjects}")
        except Exception as e:
            print(f"Warning: Stream {self.stream_name} not found.")
            print(f"Error: {e}")
    
    async def _setup_kv_store(self) -> None:
        """Setup Key-Value store."""
        kv_store_name = self.nats_config["kv_store_name"]
        
        try:
            self.kv = await self.js.create_key_value(config=KeyValueConfig(
                bucket=kv_store_name,
                description="Constellation global state for object tracking and threat intelligence",
                history=10,          # Keep last 10 revisions for debugging/rollback
                ttl=86400,          # 24 hours (increased from 1 hour for operational visibility)
                max_value_size=1048576  # 1MB max size for large detection batches
            ))
            print(f"Created/connected to KV store: {kv_store_name}")
        except Exception as e:
            try:
                self.kv = await self.js.key_value(kv_store_name)
                print(f"Connected to existing KV store: {kv_store_name}")
            except Exception as e2:
                print(f"Error accessing KV store: {e2}")
                print("Continuing without KV store")
    
    async def _publish_bootsequence(self) -> None:
        """Publish bootsequence event using publisher abstraction."""
        bootsequence_message = self.publisher.build_bootsequence(
            fingerprint=self.device_fingerprint,
            message=f"Overwatch ISR component initialized: {self.device_fingerprint['component']['type']}"
        )
        
        try:
            ack = await self.js.publish(
                self.subject,
                json.dumps(bootsequence_message).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Event-Type": "bootsequence"
                }
            )
            print(f"Published bootsequence event to JetStream")
            print(f"  Stream: {ack.stream}, Seq: {ack.seq}")
        except Exception as e:
            print(f"Error publishing bootsequence: {e}")
    
    async def publish_detection_event(self, detection_data: Dict[str, Any]) -> None:
        """Publish detection event to JetStream using publisher abstraction."""
        if not self.js:
            return

        try:
            message = self.publisher.build_detection(detection_data)
            
            headers = {
                "Content-Type": "application/json",
                "Event-Type": "detection",
                "Device-ID": self.device_fingerprint['device_id']
            }
            
            # Add threat level header for C4ISR mode
            threat_level = detection_data.get("threat_level") or detection_data.get("metadata", {}).get("threat_level")
            if threat_level:
                headers["Threat-Level"] = threat_level
                headers["Label"] = detection_data.get("label", "unknown")
            
            await self.js.publish(
                self.subject,
                json.dumps(message).encode(),
                headers=headers
            )
        except Exception as e:
            print(f"Error publishing detection event: {e}")
    
    async def publish_state_to_kv(self, tracking_state: Any, analytics: Dict[str, Any]) -> None:
        """Publish tracking state to KV store."""
        if not self.kv or not self.entity_id:
            return
        
        try:
            # Prepare state data
            state_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "entity_id": self.entity_id,
                "device_id": self.device_fingerprint['device_id'],
                "analytics": analytics
            }
            
            # Add tracking objects if available
            if hasattr(tracking_state, 'get_persistent_objects'):
                persistent_objects = tracking_state.get_persistent_objects(min_frames=3)
                state_data["tracked_objects"] = {
                    str(tid): {
                        "track_id": obj.get("track_id", obj.get("segment_id", tid)),
                        "label": obj.get("label", "segment"),
                        "first_seen": obj["first_seen"],
                        "last_seen": obj["last_seen"],
                        "frame_count": obj["frame_count"],
                        "avg_confidence": obj.get("avg_confidence", 0),
                        "is_active": obj["is_active"],
                        "threat_level": obj.get("threat_level"),
                        "suspicious_indicators": obj.get("suspicious_indicators", []),
                        "area": obj.get("area"),
                        "current_bbox": obj.get("bbox_history", [])[-1] if obj.get("bbox_history") else obj.get("bbox")
                    }
                    for tid, obj in persistent_objects.items()
                }
            
            # Store in KV with hierarchical key using helper
            key = build_kv_key(self.entity_id, "detections", "objects")
            await self.kv.put(key, json.dumps(state_data).encode())

            # Store analytics separately
            analytics_key = build_kv_key(self.entity_id, "analytics", "summary")
            await self.kv.put(analytics_key, json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "entity_id": self.entity_id,
                **analytics
            }).encode())
            
        except Exception as e:
            print(f"Error publishing state to KV: {e}")
    
    async def publish_threat_intelligence(self, tracking_state: Any) -> None:
        """Publish C4ISR threat intelligence to KV store."""
        if not self.kv or not hasattr(tracking_state, 'threat_alerts'):
            return
        
        try:
            analytics = tracking_state.get_analytics()
            threat_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "entity_id": self.entity_id,
                "device_id": self.device_fingerprint['device_id'],
                "mission": "C4ISR",
                "analytics": analytics,
                "threat_summary": {
                    "total_threats": analytics.get("active_threat_count", 0),
                    "threat_distribution": analytics.get("threat_distribution", {}),
                    "alert_level": "HIGH" if analytics.get("threat_distribution", {}).get("HIGH_THREAT", 0) > 0 else "NORMAL"
                },
                "threat_alerts": analytics.get("threat_alerts", [])
            }
            
            # Store threat intelligence using helper
            key = build_kv_key(self.entity_id, "c4isr", "threat_intelligence")
            await self.kv.put(key, json.dumps(threat_data).encode())
            
        except Exception as e:
            print(f"Error publishing threat intelligence to KV: {e}")
    
    async def cleanup(self, final_analytics: Optional[Dict] = None) -> None:
        """Clean up connections and publish shutdown event using publisher abstraction."""
        if self.js and self.device_fingerprint:
            shutdown_message = self.publisher.build_shutdown(
                message="Overwatch ISR component shutting down gracefully",
                final_analytics=final_analytics
            )
            
            try:
                ack = await self.js.publish(
                    self.subject,
                    json.dumps(shutdown_message).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Event-Type": "shutdown"
                    }
                )
                print(f"Published shutdown event to JetStream (Seq: {ack.seq})")
            except Exception as e:
                print(f"Error publishing shutdown event: {e}")
        
        if self.nc:
            await self.nc.drain()
            await self.nc.close()
            print("NATS connection closed")