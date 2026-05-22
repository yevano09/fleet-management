"""
Fleet Commander Device Simulator

Simulates IoT devices that:
  - Register with the backend via MQTT
  - Send periodic heartbeats
  - Receive OTA commands and simulate the update lifecycle
  - Handle SHA256 hash mismatches with automatic rollback
"""

import asyncio
import json
import logging
import os
import random
import signal
import sys
import time
import uuid

import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("simulator")

MQTT_BROKER_HOST = os.environ.get("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.environ.get("MQTT_BROKER_PORT", 1883))
BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")
DEVICE_COUNT = int(os.environ.get("SIMULATOR_DEVICE_COUNT", 5))
HEARTBEAT_INTERVAL = int(os.environ.get("SIMULATOR_HEARTBEAT_INTERVAL", 10))
OTA_FAILURE_RATE = float(os.environ.get("SIMULATOR_OTA_FAILURE_RATE", "0.2"))
INITIAL_FIRMWARE = "1.0.0"


class SimulatedDevice:
    def __init__(self, device_id: str, name: str):
        self.id = device_id
        self.name = name
        self.firmware_version = INITIAL_FIRMWARE
        self.previous_firmware = INITIAL_FIRMWARE
        self.status = "offline"
        self.signal_strength = random.randint(-90, -40)
        self.uptime = 100.0
        self.start_time = time.time()

        self._client = mqtt.Client(
            client_id=f"sim-{device_id[:8]}",
            protocol=mqtt.MQTTv5,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._connected = False
        self._running = False

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            logger.info(f"[{self.name}] Connected to MQTT broker")
            self._connected = True
            topic = f"iot/fleet/{self.id}/command/ota"
            client.subscribe(topic, qos=1)
            config_topic = f"iot/fleet/{self.id}/command/config"
            client.subscribe(config_topic, qos=1)
        else:
            logger.error(f"[{self.name}] MQTT connection failed: rc={reason_code}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            if msg.topic.endswith("/command/ota"):
                logger.info(f"[{self.name}] Received OTA command: {payload.get('firmware_url', '')}")
                asyncio.run_coroutine_threadsafe(
                    self._handle_ota_command(payload), self._loop
                )
            elif msg.topic.endswith("/command/config"):
                logger.info(f"[{self.name}] Received remote config: {payload.get('config', {})}")
        except Exception as e:
            logger.error(f"[{self.name}] Error processing command: {e}")

    async def _handle_ota_command(self, payload: dict):
        firmware_url = payload.get("firmware_url", "")
        expected_hash = payload.get("sha256_hash", "")
        deployment_id = str(uuid.uuid4())

        logger.info(f"[{self.name}] Starting OTA: {firmware_url}")

        await self._publish_ota_status("downloading", deployment_id)
        await asyncio.sleep(random.uniform(1.0, 3.0))

        await self._publish_ota_status("applying", deployment_id)
        await asyncio.sleep(random.uniform(1.0, 2.0))

        await self._publish_ota_status("verifying", deployment_id)
        await asyncio.sleep(random.uniform(0.5, 1.5))

        # Simulate OTA failure/rollback based on failure rate
        if random.random() < OTA_FAILURE_RATE:
            logger.warning(f"[{self.name}] SHA256 hash mismatch! Rolling back.")
            await self._publish_ota_status("hash_mismatch", deployment_id, error="SHA256 hash mismatch")
            await asyncio.sleep(0.5)
            await self._publish_ota_status("rollback", deployment_id)
            await asyncio.sleep(1.0)
            self.firmware_version = self.previous_firmware
            await self._publish_ota_status("rolled_back", deployment_id)
        else:
            self.previous_firmware = self.firmware_version
            self.firmware_version = payload.get("new_version", self.firmware_version)
            await self._publish_ota_status("success", deployment_id)
            logger.info(f"[{self.name}] OTA success, firmware: {self.firmware_version}")

    async def _publish_ota_status(self, status: str, deployment_id: str, error: str = None):
        payload = {
            "status": status,
            "deployment_id": deployment_id,
            "device_id": self.id,
            "timestamp": time.time(),
        }
        if error:
            payload["error"] = error
        topic = f"iot/fleet/{self.id}/status/ota"
        result = self._client.publish(topic, json.dumps(payload), qos=1)
        if result.rc != 0:
            logger.warning(f"[{self.name}] Failed to publish OTA status: {status}")

    async def register(self):
        payload = json.dumps({
            "device_id": self.id,
            "name": self.name,
            "firmware_version": self.firmware_version,
            "ip_address": f"10.0.0.{random.randint(1, 254)}",
        })
        self._client.publish("iot/fleet/register", payload, qos=1)
        self.status = "online"
        logger.info(f"[{self.name}] Registered")

    async def send_heartbeat(self):
        self.uptime = min(100.0, 100.0 * (1.0 - (time.time() - self.start_time) / 86400) + 95.0)
        self.signal_strength = random.randint(max(-95, self.signal_strength - 2), min(-30, self.signal_strength + 2))

        payload = json.dumps({
            "uptime_percentage": round(self.uptime, 1),
            "signal_strength": self.signal_strength,
        })
        topic = f"iot/fleet/{self.id}/heartbeat"
        self._client.publish(topic, payload, qos=1)

    def connect(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
        self._client.loop_start()

    def disconnect(self):
        self._running = False
        self._client.loop_stop()
        self._client.disconnect()

    async def register_with_retry(self, retries=3, gap=3):
        for attempt in range(retries):
            if attempt > 0:
                await asyncio.sleep(gap)
            await self.register()

    async def run(self):
        self._running = True
        self.connect(asyncio.get_event_loop())
        await self.register_with_retry()

        while self._running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await self.send_heartbeat()

        self.disconnect()


async def main():
    logger.info(f"Starting device simulator with {DEVICE_COUNT} devices")
    devices = []

    for i in range(DEVICE_COUNT):
        device_id = str(uuid.uuid4())
        name = f"Device-{i+1:03d}"
        device = SimulatedDevice(device_id, name)
        devices.append(device)
        asyncio.create_task(device.run())
        logger.info(f"Created simulated device: {name} ({device_id[:8]}...)")

    def shutdown():
        logger.info("Shutting down simulator...")
        for d in devices:
            d.disconnect()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown)
        except NotImplementedError:
            pass

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        shutdown()


if __name__ == "__main__":
    asyncio.run(main())
