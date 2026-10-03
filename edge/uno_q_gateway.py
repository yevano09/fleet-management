"""UNO Q edge gateway (P0-E): STM32 serial -> IsolationForest ONNX -> MQTT.

Runs on the QRB Linux SoC half of the Arduino UNO Q. Reads sensor JSON lines
from the STM32 MCU over UART, feeds them into the same scoring pipeline as
edge/gateway.py, and publishes compact verdicts to `iot/fleet/{id}/edge`.

The UNO Q runs the FULL edge contract — same as the RPi/x86 gateway — because
the QRB SoC has enough RAM for the ONNX IsolationForest model. Unlike the
ESP32 hub (threshold engine only), the UNO Q can join the cloud's model
registry and receive MOTA-01 model OTA updates.

Env: EDGE_BROKER_HOST/PORT, EDGE_BROKER_USER/PASS,
EDGE_MODEL_DIR (thresholds.json + model.onnx), EDGE_OUTBOX,
UNO_Q_DEVICE_ID (mqtt client id = device id), UNO_Q_UART (default /dev/ttyHS1),
UNO_Q_WINDOW (default 24), UNO_Q_MIN_POINTS (default 5).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from edge.gateway import (  # noqa: E402
    MODEL_DIR,
    MIN_POINTS,
    OUTBOX,
    WINDOW,
    Outbox,
    load_pack_or_wait,
    score_window,
)

logging.basicConfig(level=os.environ.get("EDGE_LOG_LEVEL", "INFO"))
logger = logging.getLogger("uno-q-gateway")

BROKER_HOST = os.environ.get("EDGE_BROKER_HOST", "mosquitto")
BROKER_PORT = int(os.environ.get("EDGE_BROKER_PORT", "1883"))
DEVICE_ID = os.environ.get("UNO_Q_DEVICE_ID", "uno-q-001")
UART_DEV = os.environ.get("UNO_Q_UART", "/dev/ttyHS1")
WINDOW = int(os.environ.get("UNO_Q_WINDOW", str(WINDOW)))
MIN_POINTS = int(os.environ.get("UNO_Q_MIN_POINTS", str(MIN_POINTS)))
RISK_MEDIUM = 0.4


def _uart_to_point(line: str) -> dict | None:
    """Parse one STM32 sensor line into a telemetry point dict."""
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return None
    if d.get("node", "").startswith("uno-q"):
        return None  # skip capability announcement
    now = datetime.now(timezone.utc).isoformat()
    return {
        "timestamp": now,
        "temperature": d.get("t"),
        "humidity_pct": d.get("h"),
        "shock_g": d.get("g"),
        "source": "uno-q-stm32",
    }


def main() -> None:
    import paho.mqtt.client as mqtt
    import serial

    pack, session = load_pack_or_wait()
    outbox = Outbox(OUTBOX)
    window: deque = deque(maxlen=WINDOW)
    state = {"connected": False}

    client = mqtt.Client(protocol=mqtt.MQTTv5)
    user = os.environ.get("EDGE_BROKER_USER", "")
    if user:
        client.username_pw_set(user, os.environ.get("EDGE_BROKER_PASS", ""))

    def publish(topic: str, payload: str) -> bool:
        if not state["connected"]:
            return False
        return client.publish(topic, payload, qos=1).rc == 0

    def on_connect(c, u, f, rc, props=None):
        state["connected"] = rc == 0
        if state["connected"]:
            logger.info("UNO Q gateway connected to %s:%s as %s", BROKER_HOST, BROKER_PORT, DEVICE_ID)
            n = outbox.replay(publish)
            if n:
                logger.info("replayed %d buffered verdicts", n)

    def on_disconnect(c, u, f, rc, props=None):
        state["connected"] = False
        logger.warning("UNO Q disconnected rc=%s, buffering locally", rc)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()

    try:
        ser = serial.Serial(UART_DEV, 115200, timeout=1)
    except Exception:
        logger.warning("UART %s unavailable, falling back to simulated sensors", UART_DEV)
        ser = None

    logger.info("UNO Q gateway started: device=%s uart=%s window=%d min_pts=%d",
                DEVICE_ID, UART_DEV if ser else "sim", WINDOW, MIN_POINTS)

    sim_t = 25.0
    while True:
        points: list[dict] = []
        if ser:
            try:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                p = _uart_to_point(line)
                if p:
                    points.append(p)
            except serial.SerialException:
                logger.warning("UART read error, retry in 1s")
                time.sleep(1)
                continue
        else:
            # Simulated fallback: slow thermal drift so demos produce data.
            sim_t += 0.05
            sim_t = max(20.0, min(70.0, sim_t))
            points.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "temperature": round(sim_t, 1),
                "humidity_pct": 65.0,
                "shock_g": 0.2,
                "source": "uno-q-sim",
            })
            time.sleep(0.5)

        for p in points:
            window.append(p)
        if len(window) < MIN_POINTS:
            continue

        risk, risk_type, s = score_window(list(window), pack, session)
        if risk < RISK_MEDIUM:
            continue
        verdict = {
            "risk_type": risk_type,
            "risk_score": risk,
            "model_version": pack["version"] + "+uno-q",
            "window_end": datetime.now(timezone.utc).isoformat(),
            "anomaly_score": s,
            "replayed": False,
        }
        topic = f"iot/fleet/{DEVICE_ID}/edge"
        body = json.dumps(verdict)
        if publish(topic, body):
            logger.info("verdict %s %s %.2f", DEVICE_ID, risk_type, risk)
        else:
            outbox.spill(topic, body)


if __name__ == "__main__":
    main()
