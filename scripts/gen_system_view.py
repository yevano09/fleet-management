"""Generate a system-view Excalidraw diagram for Fleet Commander."""
import json
import os

def rect(id, x, y, w, h, stroke, bg, text="", font_size=14, opacity=30, roundness=True):
    el = {
        "id": id, "type": "rectangle", "x": x, "y": y, "width": w, "height": h,
        "strokeColor": stroke, "backgroundColor": bg, "fillStyle": "solid",
        "strokeWidth": 2, "roughness": 0, "opacity": opacity,
        "version": 1, "versionNonce": hash(id) % 1000000,
        "isDeleted": False, "strokeStyle": "solid", "angle": 0,
        "seed": hash(id) % 10000, "groupIds": [], "frameId": None,
        "boundElements": [], "updated": 1779468853873, "link": None, "locked": False,
    }
    if roundness:
        el["roundness"] = {"type": 3}
    els = [el]
    if text:
        els.append(text_el(id + "_t", x + w//2 - len(text)*font_size*0.3 // 2, y + h//2 - font_size*0.7, text, font_size))
    return els

def text_el(id, x, y, text, font_size=14, font_family=1, align="center", color="#1e1e1e"):
    return {
        "id": id, "type": "text", "x": x, "y": y, "width": len(text)*font_size*0.6,
        "height": font_size*1.5, "text": text, "fontSize": font_size, "fontFamily": font_family,
        "textAlign": align, "strokeColor": color, "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 2, "roundness": None, "roughness": 0,
        "opacity": 100, "version": 1, "versionNonce": hash(id) % 1000000,
        "isDeleted": False, "strokeStyle": "solid", "angle": 0,
        "seed": hash(id) % 10000, "groupIds": [], "frameId": None,
        "boundElements": [], "updated": 1779468853873, "link": None, "locked": False,
        "verticalAlign": "top", "containerId": None, "originalText": text, "autoResize": False,
        "lineHeight": 1.25,
    }

def arrow(id, x, y, dx, dy, color="#1e1e1e"):
    return {
        "id": id, "type": "arrow", "x": x, "y": y, "width": abs(dx), "height": abs(dy),
        "points": [[0, 0], [dx, dy]], "strokeColor": color, "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 2, "roughness": 0, "opacity": 100,
        "version": 1, "versionNonce": hash(id) % 1000000,
        "isDeleted": False, "strokeStyle": "solid", "angle": 0,
        "seed": hash(id) % 10000, "groupIds": [], "frameId": None,
        "boundElements": [], "updated": 1779468853873, "link": None, "locked": False,
        "startArrowhead": None, "endArrowhead": "arrow",
    }

elements = []

# Title
elements.append(text_el("title", 350, 20, "Fleet Commander — System View", 28, 1, "center"))

# ═══ TOP ROW: Dashboard + Monitoring ═══
elements += rect("dash", 450, 80, 260, 60, "#1c7ed6", "#d0ebff", "Dashboard UI\nJinja2 + HTMX + Chart.js + Leaflet", 13, 40)
elements += rect("prom", 780, 80, 200, 60, "#e67700", "#ffec99", "Prometheus\n:9090 · ~49 metrics", 13, 40)
elements += rect("grafana", 1010, 80, 200, 60, "#fd7e14", "#ffd8a8", "Grafana\n:3050 · 13+ panels", 13, 40)

# ═══ BACKEND BOX ═══
bx, by, bw, bh = 60, 190, 1100, 340
elements += rect("backend", bx, by, bw, bh, "#7048e8", "#e5dbff", "", 14, 15)
elements.append(text_el("backend_t", bx + bw//2 - 200, by + 8, "FastAPI Backend (:8000) — REST + MQTT + Agents + Schedulers", 16, 1, "center", "#7048e8"))

# Backend sub-components (2 rows of cards)
subs = [
    ("REST Routers\n25 modules · 100+ routes\nRBAC + tenancy scoped", 90, 230, 180, 70),
    ("MQTT Handlers\nregister/heartbeat/obd/bms\n/cargo/status/v2g", 290, 230, 180, 70),
    ("ML Registry\nIsolationForest · joblib\n?model=auto|ml|legacy", 490, 230, 180, 70),
    ("Aegis Engine\n8 rules · scrape 15s\nclassify→decide→act", 690, 230, 170, 70),
    ("OTA Scheduler\ncanary + blackout\n30s tick loop", 880, 230, 160, 70),
    ("Copilot Agent\nCrewAI · mock/real LLM\nprivacy redaction", 1060, 230, 160, 70),
    ("Command Queue\nTTL + retry · 15s flush\non reconnect", 90, 320, 180, 70),
    ("Telemetry Worker\nbounded batch 200/1s\ntenant+region stamped", 290, 320, 180, 70),
    ("Retention Worker\n24h-hot + 5m rollups\n7d expiry · 10min tick", 490, 320, 180, 70),
    ("Alert Engine\ndedup/cooldown/escalate\n+/WO auto-create", 690, 320, 170, 70),
    ("Digital Twin\nGET /twin/{id}\nhealth + vehicle + cargo", 880, 320, 160, 70),
    ("Audit + Webhooks\naudit trail\nevent emitter HMAC", 1060, 320, 160, 70),
]
for label, x, y, w, h in subs:
    lines = label.split("\n")
    elements += rect(f"sub_{x}_{y}", x, y, w, h, "#9775fa", "#f3f0ff", "", 12, 30)
    for i, line in enumerate(lines):
        elements.append(text_el(f"sub_{x}_{y}_t{i}", x + 8, y + 8 + i*16, line, 12, 1, "left", "#5f3dc4"))

# ═══ DATABASE ═══
elements += rect("db", 160, 580, 220, 70, "#0b7285", "#c5f6fa", "SQLite/PostgreSQL\n30+ tables · Alembic\nasync SQLAlchemy", 13, 40)

# ═══ MQTT BROKER ═══
elements += rect("mqtt", 450, 580, 220, 70, "#e8590c", "#ffe8cc", "Mosquitto MQTT\n:1883 anon / :8883 mTLS\n+/obd +/bms +/cargo +/edge", 13, 40)

# ═══ ALERT CHANNELS ═══
elements += rect("alerts", 740, 580, 200, 70, "#c92a2a", "#ffc9c9", "Alert Channels\nSlack · SMTP · Webhook\nHMAC-signed delivery", 13, 40)

# ═══ WEBHOOKS / EVENT ═══
elements += rect("events", 1000, 580, 180, 70, "#862e9c", "#f3d9fa", "Event Emitter\nWebhook fan-out\nHMAC signing", 13, 40)

# ═══ DEVICES (bottom) ═══
elements += rect("sim", 100, 720, 220, 80, "#2b8a3e", "#d3f9d8", "Device Simulator\n5 virtual EVs\nOBD/BMS/cargo feeds\nfault scenarios", 13, 40)
elements += rect("esp32", 380, 720, 200, 80, "#2b8a3e", "#d3f9d8", "ESP32 Hub\nthreshold engine\ncargo frames + OTA\nWiFi/MQTT", 13, 40)
elements += rect("arduino", 640, 720, 180, 80, "#2b8a3e", "#d3f9d8", "Arduino Node\nDHT22+MPU6050\n@2Hz serial → ESP32", 13, 40)
elements += rect("edgegw", 880, 720, 220, 80, "#0b7285", "#c5f6fa", "Edge Gateway (RPi/x86)\nONNX IsolationForest\nsqlite outbox · replay", 13, 40)

# ═══ ARROWS ═══
elements.append(arrow("a1", 580, 140, 0, 50, "#1c7ed6"))       # dash → backend
elements.append(arrow("a2", 780, 140, -180, 50, "#e67700"))    # prom scrapes backend
elements.append(arrow("a3", 1010, 140, -400, 50, "#fd7e14"))   # grafana queries prom
elements.append(arrow("a4", 270, 530, 0, 50, "#0b7285"))       # backend → db
elements.append(arrow("a5", 560, 530, 0, 50, "#e8590c"))       # backend → mqtt
elements.append(arrow("a6", 840, 530, 0, 50, "#c92a2a"))       # backend → alerts
elements.append(arrow("a7", 1090, 530, 0, 50, "#862e9c"))      # backend → events
elements.append(arrow("a8", 560, 650, -330, 70, "#e8590c"))    # mqtt → sim
elements.append(arrow("a9", 560, 650, -100, 70, "#e8590c"))    # mqtt → esp32
elements.append(arrow("a10", 560, 650, 100, 70, "#e8590c"))    # mqtt → arduino (via esp32?)
elements.append(arrow("a11", 560, 650, 400, 70, "#0b7285"))    # mqtt → edgegw
elements.append(arrow("a12", 480, 800, 160, 0, "#2b8a3e"))     # esp32 → arduino? actually arduino → esp32
elements[-1]["startArrowhead"] = "arrow"
elements[-1]["endArrowhead"] = None

# Legend box
elements += rect("legend", 160, 860, 1000, 60, "#868e96", "#f1f3f5", "", 11, 20)
legend_items = [
    ("User-facing", "#1c7ed6"), ("Monitoring", "#e67700"), ("Backend service", "#7048e8"),
    ("Data store", "#0b7285"), ("MQTT broker", "#e8590c"), ("Device/Edge", "#2b8a3e"),
    ("Alerts/Events", "#c92a2a"),
]
for i, (label, color) in enumerate(legend_items):
    elements += rect(f"leg{i}", 180 + i*145, 878, 20, 14, color, "transparent", "", 0, 0)
    elements.append(text_el(f"leg{i}_t", 206 + i*145, 878, label, 11, 1, "left", color))

doc = {
    "type": "excalidraw",
    "version": 2,
    "source": "https://github.com/fleet-commander",
    "elements": elements,
    "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
    "files": {},
}

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "system-view.excalidraw")
out = os.path.normpath(out)
with open(out, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2)
print(f"Written {out} ({len(elements)} elements)")
