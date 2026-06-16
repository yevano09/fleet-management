from prometheus_client import Counter, Gauge, Histogram
import time
from functools import wraps
from fastapi import Request

# Device metrics
active_devices = Gauge("fleet_active_devices", "Number of currently online devices")
total_devices = Gauge("fleet_total_devices", "Total registered devices")

# OTA metrics
ota_deployments_total = Counter(
    "fleet_ota_deployments_total", "Total OTA deployment attempts", ["status"]
)
ota_deployments_in_progress = Gauge("fleet_ota_in_progress", "OTA deployments currently in progress")

# API metrics
api_request_latency = Histogram(
    "fleet_api_request_latency_seconds",
    "API request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# V2G metrics
v2g_active_discharges = Gauge("fleet_v2g_active_discharges", "Number of devices currently discharging V2G")
v2g_projected_revenue_dollars = Gauge("fleet_v2g_projected_revenue_dollars", "Total projected V2G arbitrage revenue")
battery_degradation_cost_dollars = Gauge("fleet_battery_degradation_cost_dollars", "Accumulated battery degradation cost")
device_soc = Gauge("fleet_device_soc", "Per-device state of charge", ["device"])

# Alert metrics
alerts_total = Counter(
    "fleet_alerts_total", "Total alerts fired", ["severity", "type"]
)
alerts_active = Gauge("fleet_alerts_active", "Currently active alerts")
alert_notifications_total = Counter(
    "fleet_alert_notifications_total", "Alert notifications sent", ["channel"]
)

# MQTT metrics
mqtt_messages_published = Counter(
    "fleet_mqtt_messages_published_total", "MQTT messages published", ["topic"]
)
mqtt_messages_received = Counter(
    "fleet_mqtt_messages_received_total", "MQTT messages received", ["topic"]
)


def track_latency(endpoint: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.time()
            try:
                return await func(*args, **kwargs)
            finally:
                elapsed = time.time() - start
                api_request_latency.labels(method="POST", endpoint=endpoint).observe(elapsed)
        return wrapper
    return decorator


async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start
    api_request_latency.labels(
        method=request.method, endpoint=request.url.path
    ).observe(elapsed)
    return response
