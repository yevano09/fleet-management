import json
import logging
from typing import Optional, Callable
from datetime import datetime, timezone
import asyncio
from threading import Thread

import paho.mqtt.client as mqtt

from app.config import settings

logger = logging.getLogger(__name__)

MQTT_TOPIC_COMMAND_OTA = "iot/fleet/{device_id}/command/ota"
MQTT_TOPIC_STATUS_OTA = "iot/fleet/{device_id}/status/ota"
MQTT_TOPIC_HEARTBEAT = "iot/fleet/{device_id}/heartbeat"
MQTT_TOPIC_REGISTER = "iot/fleet/register"


class MqttClient:
    def __init__(self):
        self.client: Optional[mqtt.Client] = None
        self._connected = False
        self._on_ota_status: Optional[Callable] = None
        self._on_heartbeat: Optional[Callable] = None
        self._on_register: Optional[Callable] = None
        self._on_v2g_status: Optional[Callable] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def on_ota_status(self, callback: Callable):
        self._on_ota_status = callback

    def on_heartbeat(self, callback: Callable):
        self._on_heartbeat = callback

    def on_register(self, callback: Callable):
        self._on_register = callback

    def on_v2g_status(self, callback: Callable):
        self._on_v2g_status = callback

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info("Connected to MQTT broker")
            self._connected = True
            client.subscribe("iot/fleet/+/status/ota", qos=1)
            client.subscribe("iot/fleet/+/heartbeat", qos=1)
            client.subscribe("iot/fleet/register", qos=1)
            client.subscribe("iot/fleet/+/status/v2g", qos=1)
        else:
            logger.error("Failed to connect to MQTT broker, rc=%s", reason_code)
            self._connected = False

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        logger.warning("Disconnected from MQTT broker, rc=%s", reason_code)
        self._connected = False

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            topic_parts = msg.topic.split("/")
            logger.debug(f"MQTT message received: topic={msg.topic}, parts={len(topic_parts)}")

            if msg.topic.endswith("/status/ota") and len(topic_parts) >= 5:
                device_id = topic_parts[2]
                if self._on_ota_status:
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self._on_ota_status(device_id, payload), self._loop
                        )
            elif msg.topic.endswith("/heartbeat") and len(topic_parts) >= 4:
                device_id = topic_parts[2]
                if self._on_heartbeat:
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self._on_heartbeat(device_id, payload), self._loop
                        )
            elif msg.topic.endswith("/status/v2g") and len(topic_parts) >= 5:
                device_id = topic_parts[2]
                if self._on_v2g_status:
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self._on_v2g_status(device_id, payload), self._loop
                        )
            elif msg.topic.endswith("/register"):
                if self._on_register:
                    if self._loop and self._loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self._on_register(payload), self._loop
                        )
        except Exception:
            logger.exception("Error processing MQTT message")

    def connect(self):
        self.client = mqtt.Client(
            client_id="fleet-commander-backend",
            protocol=mqtt.MQTTv5,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )

        if settings.mqtt_username and settings.mqtt_password:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

        try:
            self.client.connect(settings.mqtt_broker_host, settings.mqtt_broker_port, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            logger.warning(f"Could not connect to MQTT broker: {e}")

    def disconnect(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self._connected = False

    def publish_ota_command(self, device_id: str, firmware_url: str, sha256_hash: str, deployment_id: str = ""):
        if not self._connected:
            logger.warning("MQTT not connected, cannot publish OTA command")
            return False
        topic = MQTT_TOPIC_COMMAND_OTA.format(device_id=device_id)
        payload_dict = {
            "firmware_url": firmware_url,
            "sha256_hash": sha256_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if deployment_id:
            payload_dict["deployment_id"] = deployment_id
        payload = json.dumps(payload_dict)
        result = self.client.publish(topic, payload, qos=1)
        logger.info(f"Published OTA command to {topic}: result={result.rc}")
        return result.rc == 0

    def publish_v2g_command(self, device_id: str, action: str, power_kw: float, duration_minutes: int):
        if not self._connected:
            logger.warning("MQTT not connected, cannot publish V2G command")
            return False
        topic = f"iot/fleet/{device_id}/command/v2g"
        payload = json.dumps({
            "action": action,
            "power_kw": power_kw,
            "duration_minutes": duration_minutes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        result = self.client.publish(topic, payload, qos=1)
        logger.info("Published V2G command, rc=%s", result.rc)
        return result.rc == 0

    def publish_remote_config(self, device_id: str, config: dict):
        if not self._connected:
            logger.warning("MQTT not connected, cannot publish config")
            return False
        topic = f"iot/fleet/{device_id}/command/config"
        payload = json.dumps({
            "config": config,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        result = self.client.publish(topic, payload, qos=1)
        return result.rc == 0

    def publish_restart_command(self, device_id: str, reason: str = "soft_restart") -> bool:
        if not self._connected:
            return False
        topic = f"iot/fleet/{device_id}/command/restart"
        payload = json.dumps({"command": "restart", "reason": reason, "timestamp": datetime.now(timezone.utc).isoformat()})
        result = self.client.publish(topic, payload, qos=1)
        return result.rc == 0

    def publish_rollback_command(self, device_id: str, previous_firmware: str) -> bool:
        if not self._connected:
            return False
        topic = f"iot/fleet/{device_id}/command/rollback"
        payload = json.dumps({"command": "rollback", "previous_firmware": previous_firmware, "timestamp": datetime.now(timezone.utc).isoformat()})
        result = self.client.publish(topic, payload, qos=1)
        return result.rc == 0

    def publish_maintenance_command(self, device_id: str, enter: bool = True, reason: str = "") -> bool:
        if not self._connected:
            return False
        topic = f"iot/fleet/{device_id}/command/maintenance"
        cmd = "enter_maintenance" if enter else "exit_maintenance"
        payload = json.dumps({"command": cmd, "reason": reason, "timestamp": datetime.now(timezone.utc).isoformat()})
        result = self.client.publish(topic, payload, qos=1)
        return result.rc == 0

    def publish_shadow_desired(self, device_id: str, state: dict) -> bool:
        """Push desired shadow state to a device (Feature 7)."""
        if not self._connected:
            return False
        topic = f"iot/fleet/{device_id}/command/shadow"
        payload = json.dumps({"state": state, "timestamp": datetime.now(timezone.utc).isoformat()})
        result = self.client.publish(topic, payload, qos=1)
        return result.rc == 0

    def publish_raw(self, topic: str, payload: str, qos: int = 1) -> bool:
        """Publish to an arbitrary topic (used by offline command queue delivery)."""
        if not self._connected:
            return False
        result = self.client.publish(topic, payload, qos=qos)
        return result.rc == 0

    @property
    def is_connected(self) -> bool:
        return self._connected


mqtt_client = MqttClient()
