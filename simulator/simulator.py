"""
Fleet Commander Device Simulator

Simulates IoT devices that:
  - Register with the backend via MQTT
  - Send periodic heartbeats
  - Receive OTA commands and simulate the update lifecycle
  - Handle SHA256 hash mismatches with automatic rollback
  - Simulate EV battery behaviour for V2G (SOC, SOH, temp, plug status)
  - Respond to V2G discharge/charge commands
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


V2G_POWER_KW = 7.2
BATTERY_CAPACITY_KWH = 60.0


class SimulatedDevice:
    def __init__(self, device_id: str, name: str, is_ev: bool = False):
        self.id = device_id
        self.name = name
        self.firmware_version = INITIAL_FIRMWARE
        self.previous_firmware = INITIAL_FIRMWARE
        self.status = "offline"
        self.signal_strength = random.randint(-90, -40)
        self.uptime = 100.0
        self.start_time = time.time()

        # EV battery simulation
        self.is_ev = is_ev
        self.soc = 80.0  # percent
        self.soh = 100.0  # percent
        self.battery_temp = 25.0  # celsius
        self.plug_status = "connected"
        self._v2g_action = "idle"
        self._v2g_end_time = 0.0

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
            v2g_topic = f"iot/fleet/{self.id}/command/v2g"
            client.subscribe(v2g_topic, qos=1)
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
            elif msg.topic.endswith("/command/v2g"):
                logger.info(f"[{self.name}] Received V2G command: {payload.get('action', '')}")
                self._handle_v2g_command(payload)
        except Exception as e:
            logger.error(f"[{self.name}] Error processing command: {e}")

    async def _handle_ota_command(self, payload: dict):
        firmware_url = payload.get("firmware_url", "")
        expected_hash = payload.get("sha256_hash", "")
        deployment_id = payload.get("deployment_id", str(uuid.uuid4()))

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

    def _handle_v2g_command(self, payload: dict):
        action = payload.get("action", "idle")
        power_kw = float(payload.get("power_kw", V2G_POWER_KW))
        duration_min = int(payload.get("duration_minutes", 60))
        self._v2g_action = action
        self._v2g_end_time = time.time() + duration_min * 60
        if action == "discharge":
            self.plug_status = "discharging"
            logger.info(f"[{self.name}] V2G discharging at {power_kw}kW for {duration_min}min")
        elif action == "charge":
            self.plug_status = "charging"
            logger.info(f"[{self.name}] V2G charging at {power_kw}kW for {duration_min}min")
        else:
            self.plug_status = "connected"
            self._v2g_action = "idle"

    def _update_battery(self):
        if not self.is_ev:
            return
        now = time.time()
        # Temperature cycles slowly
        self.battery_temp += random.uniform(-0.5, 0.5)
        self.battery_temp = max(15.0, min(45.0, self.battery_temp))

        if self._v2g_action == "discharge" and now < self._v2g_end_time:
            discharge_kwh = V2G_POWER_KW * (HEARTBEAT_INTERVAL / 3600.0)
            soc_drop = (discharge_kwh / BATTERY_CAPACITY_KWH) * 100.0
            self.soc = max(10.0, self.soc - soc_drop)
            self.battery_temp += 0.2  # heat from discharge
        elif self._v2g_action == "charge" and now < self._v2g_end_time:
            charge_kwh = V2G_POWER_KW * (HEARTBEAT_INTERVAL / 3600.0)
            soc_rise = (charge_kwh / BATTERY_CAPACITY_KWH) * 100.0
            self.soc = min(95.0, self.soc + soc_rise)
            self.battery_temp += 0.1
        else:
            self._v2g_action = "idle"
            self.plug_status = "connected"
            # Slow self-discharge when idle
            self.soc = max(10.0, self.soc - 0.01)

        # SOH slowly degrades over time
        self.soh = max(70.0, self.soh - 0.001)

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
        self._update_battery()

        payload = {
            "uptime_percentage": round(self.uptime, 1),
            "signal_strength": self.signal_strength,
        }
        if self.is_ev:
            payload["soc"] = round(self.soc, 1)
            payload["soh"] = round(self.soh, 1)
            payload["battery_temp"] = round(self.battery_temp, 1)
            payload["plug_status"] = self.plug_status

        topic = f"iot/fleet/{self.id}/heartbeat"
        self._client.publish(topic, json.dumps(payload), qos=1)

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
        is_ev = i < 3  # first 3 devices are EVs with battery simulation
        name = f"Device-{i+1:03d}"
        device = SimulatedDevice(device_id, name, is_ev=is_ev)
        devices.append(device)
        asyncio.create_task(device.run())
        logger.info(f"Created simulated device: {name} ({device_id[:8]}...) is_ev={is_ev}")

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
