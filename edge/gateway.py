"""Edge AI gateway (P0-E): local verdicts, offline buffer, cloud replay.

Runs on Raspberry Pi / x86 gateway (Docker `edge` profile) or any box with
broker access. Per device it keeps a trailing telemetry window, scores it
with the exported edge pack, and publishes compact verdicts — never raw
samples — to `iot/fleet/{device_id}/edge`:

    {"risk_type": ..., "risk_score": 0.0-1.0, "model_version": ...,
     "window_end": "<iso>", "replayed": false}

Scoring mirrors app/ml/inference.py exactly:
  features (12-dim, pure python port) -> forest score (onnxruntime if the
  model file exists, else skipped) -> max(forest_risk, z_risk), floor 0.4.

Offline: verdicts spill to a local sqlite outbox; on reconnect they replay
in order with replayed:true. Cloud dedups on (device, window_end, source).

Env: EDGE_BROKER_HOST/PORT, EDGE_BROKER_USER/PASS (optional),
EDGE_MODEL_DIR (thresholds.json + model.onnx), EDGE_OUTBOX (sqlite path),
EDGE_WINDOW (default 24), EDGE_MIN_POINTS (default 5),
EDGE_DEVICE_PREFIX (only mirror these device ids; empty = all).
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

logging.basicConfig(level=os.environ.get("EDGE_LOG_LEVEL", "INFO"))
logger = logging.getLogger("edge-gateway")

BROKER_HOST = os.environ.get("EDGE_BROKER_HOST", "mosquitto")
BROKER_PORT = int(os.environ.get("EDGE_BROKER_PORT", "1883"))
MODEL_DIR = os.environ.get("EDGE_MODEL_DIR", "/app/edge-pack")
OUTBOX = os.environ.get("EDGE_OUTBOX", "/app/data/edge_outbox.db")
WINDOW = int(os.environ.get("EDGE_WINDOW", "24"))
MIN_POINTS = int(os.environ.get("EDGE_MIN_POINTS", "5"))
PREFIX = os.environ.get("EDGE_DEVICE_PREFIX", "")

RISK_MEDIUM = 0.4
TPMS_NOMINAL = 32.0
GATE_GROUPS = {
    "signal_degradation": (0, 1, 2),
    "thermal": (3, 4, 5),
    "intermittent": (6, 7),
    "tire_pressure": (10, 11),
}


def load_pack_or_wait():
    """Block until thresholds.json appears (pack baked at deploy time)."""
    path = os.path.join(MODEL_DIR, "thresholds.json")
    while not os.path.exists(path):
        logger.warning("waiting for edge pack at %s (run scripts/export_onnx.py)", path)
        time.sleep(10)
    return load_pack()


def load_pack():
    with open(os.path.join(MODEL_DIR, "thresholds.json")) as f:
        pack = json.load(f)
    session = None
    onnx_path = os.path.join(MODEL_DIR, "model.onnx")
    if os.path.exists(onnx_path):
        try:
            import onnxruntime as ort

            session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
            logger.info("ONNX model loaded from %s", onnx_path)
        except Exception:
            logger.warning("onnxruntime unavailable, z-gate only", exc_info=True)
    else:
        logger.info("no model.onnx in %s, z-gate only", MODEL_DIR)
    return pack, session


def _slope(values):
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    den = sum((x - mean_x) ** 2 for x in range(n))
    if not den:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in enumerate(values)) / den


def _var(values):
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return sum((v - mean) ** 2 for v in values) / n


def _col(points, key):
    return [float(p[key]) for p in points if p.get(key) is not None]


def _tpms_min(points):
    best = None
    for p in points:
        tires = p.get("tire_pressures") or {}
        if isinstance(tires, dict):
            for v in tires.values():
                if v is not None and (best is None or float(v) < best):
                    best = float(v)
    return best if best is not None else TPMS_NOMINAL


def series_to_features(points):
    sig = _col(points, "signal_strength")
    tmp = _col(points, "temperature")
    cpu = _col(points, "cpu_usage")
    upt = _col(points, "uptime_percentage")
    fuel = _col(points, "fuel_level_pct")
    dtc = sum(1 for p in points if p.get("dtc_codes"))
    tpms_min = _tpms_min(points)
    return [
        _slope(sig), _var(sig), min(sig) if sig else -60.0,
        _slope(tmp), max(tmp) if tmp else 45.0, _var(tmp),
        _var(cpu),
        sum(1 for u in upt if u < 95.0) / len(upt) if upt else 0.0,
        float(dtc), _slope(fuel), tpms_min,
        max(0.0, TPMS_NOMINAL - tpms_min),
    ]


def score_window(points, pack, session):
    """Return (risk, risk_type, anomaly_score|None) for a trailing window."""
    feats = series_to_features(points)
    mean, std = pack["mean"], pack["std"]
    s = None
    forest_risk = 0.0
    if session is not None and "onnx_score_shift" in pack:
        import numpy as np

        raw = session.run(None, {"input": np.asarray([feats], dtype=np.float32)})
        by_name = {o.name: v.ravel()[0] for o, v in zip(session.get_outputs(), raw)}
        s = float(by_name.get("scores", list(by_name.values())[0])) + pack["onnx_score_shift"]
        thr = pack["score_mean"] - pack.get("forest_threshold_sigma", 2.5) * pack["score_std"]
        if s < thr:
            forest_risk = min(1.0, RISK_MEDIUM + (1.0 - RISK_MEDIUM)
                              * (thr - s) / (pack.get("forest_span_sigma", 5.0) * pack["score_std"]))
    best_group, best_z = "anomaly", 0.0
    for group, idxs in GATE_GROUPS.items():
        z = max(abs((feats[i] - mean[i]) / std[i]) for i in idxs)
        if z > best_z:
            best_z, best_group = z, group
    zq, zf = pack.get("z_quiet", 3.0), pack.get("z_full", 6.0)
    z_risk = 0.0 if best_z <= zq else min(1.0, RISK_MEDIUM + (1.0 - RISK_MEDIUM) * (best_z - zq) / (zf - zq))
    risk = round(max(forest_risk, z_risk), 3)
    return risk, best_group, (round(s, 4) if s is not None else None)


class Outbox:
    """Durable spillover for verdicts while the broker is unreachable.

    Thread-safe: the paho network thread (spill via on_message) and the
    reconnect path (replay via on_connect) share one sqlite connection, so
    every statement runs under a single lock.
    """

    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS verdicts "
                            "(id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, payload TEXT)")

    def spill(self, topic, payload):
        with self._lock:
            self.db.execute("INSERT INTO verdicts (topic, payload) VALUES (?, ?)", (topic, payload))
            self.db.commit()

    def replay(self, publish):
        with self._lock:
            rows = self.db.execute("SELECT id, topic, payload FROM verdicts ORDER BY id").fetchall()
            for rid, topic, payload in rows:
                try:
                    doc = json.loads(payload)
                    doc["replayed"] = True
                    if publish(topic, json.dumps(doc)):
                        self.db.execute("DELETE FROM verdicts WHERE id = ?", (rid,))
                except Exception:
                    logger.exception("replay of outbox row %s failed", rid)
                    break
            self.db.commit()
            return len(rows)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def main():
    import paho.mqtt.client as mqtt

    pack, session = load_pack_or_wait()
    windows: dict[str, deque] = defaultdict(lambda: deque(maxlen=WINDOW))
    outbox = Outbox(OUTBOX)
    client = mqtt.Client(protocol=mqtt.MQTTv5)
    user = os.environ.get("EDGE_BROKER_USER", "")
    if user:
        client.username_pw_set(user, os.environ.get("EDGE_BROKER_PASS", ""))
    state = {"connected": False, "verdicts": 0, "replayed": 0}

    def publish(topic, payload):
        if not state["connected"]:
            return False
        rc = client.publish(topic, payload, qos=1).rc
        return rc == 0

    def on_connect(c, u, f, rc, props=None):
        state["connected"] = rc == 0
        if state["connected"]:
            logger.info("edge gateway connected to %s:%s", BROKER_HOST, BROKER_PORT)
            for t in ("iot/fleet/+/heartbeat", "iot/fleet/+/obd"):
                c.subscribe(t, qos=1)
            n = outbox.replay(publish)
            state["replayed"] += n
            if n:
                logger.info("replayed %d buffered verdicts", n)

    def on_disconnect(c, u, f, rc, props=None):
        state["connected"] = False
        logger.warning("edge gateway disconnected rc=%s, buffering locally", rc)

    def on_message(c, u, msg):
        try:
            parts = msg.topic.split("/")
            if len(parts) < 4:
                return
            device_id = parts[2]
            if PREFIX and not device_id.startswith(PREFIX):
                return
            payload = json.loads(msg.payload.decode())
            payload.setdefault("source", "edge-mirror")
            windows[device_id].append(payload)
            pts = list(windows[device_id])
            if len(pts) < MIN_POINTS:
                return
            risk, risk_type, s = score_window(pts, pack, session)
            if risk < RISK_MEDIUM:
                return
            verdict = {"risk_type": risk_type, "risk_score": risk,
                       "model_version": pack["version"] + "+edge",
                       "window_end": now_iso(), "anomaly_score": s, "replayed": False}
            topic = f"iot/fleet/{device_id}/edge"
            body = json.dumps(verdict)
            if publish(topic, body):
                state["verdicts"] += 1
                logger.info("verdict %s %s %.2f", device_id, risk_type, risk)
            else:
                outbox.spill(topic, body)
        except Exception:
            logger.exception("edge scoring failed")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
