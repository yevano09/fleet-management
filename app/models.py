import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Text, Enum as SAEnum, Boolean, Index
from sqlalchemy.orm import relationship
from app.database import Base
from app.utils import utcnow


class DeviceStatus(str, enum.Enum):
    online = "online"
    offline = "offline"


class OtaStatus(str, enum.Enum):
    pending = "pending"
    downloading = "downloading"
    applying = "applying"
    verifying = "verifying"
    success = "success"
    hash_mismatch = "hash_mismatch"
    rollback = "rollback"
    rolled_back = "rolled_back"
    failed = "failed"


class V2gAction(str, enum.Enum):
    idle = "idle"
    charge = "charge"
    discharge = "discharge"


class DeviceLifecycle(str, enum.Enum):
    active = "active"
    maintenance = "maintenance"
    decommissioned = "decommissioned"


class CommandStatus(str, enum.Enum):
    queued = "queued"
    delivered = "delivered"
    expired = "expired"
    failed = "failed"


class ScheduleStatus(str, enum.Enum):
    scheduled = "scheduled"
    running = "running"
    completed = "completed"
    cancelled = "cancelled"
    paused = "paused"
    failed = "failed"


class GeofenceShape(str, enum.Enum):
    circle = "circle"
    polygon = "polygon"


class UserRole(str, enum.Enum):
    user = "user"
    admin = "admin"
    operator = "operator"
    viewer = "viewer"
    fleet_manager = "fleet_manager"


class Device(Base):
    __tablename__ = "devices"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    firmware_version = Column(String, default="1.0.0")
    status = Column(SAEnum(DeviceStatus), default=DeviceStatus.offline)
    signal_strength = Column(Integer, default=0)
    last_seen = Column(DateTime, default=utcnow)
    uptime_percentage = Column(Float, default=100.0)
    ip_address = Column(String, default="")
    previous_firmware_version = Column(String, nullable=True)
    current_ota_id = Column(String, nullable=True)
    mqtt_client_id = Column(String, nullable=True)

    # GPS / location fields
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    city = Column(String, nullable=True)

    # V2G / EV battery fields
    soc = Column(Float, default=80.0)       # state of charge percent
    soh = Column(Float, default=100.0)      # state of health percent
    battery_temp = Column(Float, default=25.0)  # celsius
    plug_status = Column(String, default="disconnected")  # disconnected, connected, charging

    # Lifecycle management (Feature 9)
    lifecycle_status = Column(SAEnum(DeviceLifecycle), default=DeviceLifecycle.active)
    decommissioned_at = Column(DateTime, nullable=True)
    decommissioned_by = Column(String, nullable=True)
    decommissioned_reason = Column(Text, nullable=True)
    claim_token = Column(String, nullable=True)  # QR-claim provisioning token

    ota_deployments = relationship("OtaDeployment", back_populates="device")
    v2g_schedules = relationship("V2gSchedule", back_populates="device")
    telemetry = relationship("Telemetry", back_populates="device", cascade="all, delete-orphan")
    shadows = relationship("DeviceShadow", back_populates="device", cascade="all, delete-orphan")
    geofence_events = relationship("GeofenceEvent", back_populates="device", cascade="all, delete-orphan")
    predicted_failures = relationship("PredictedFailure", back_populates="device", cascade="all, delete-orphan")


class V2gSchedule(Base):
    __tablename__ = "v2g_schedules"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String, ForeignKey("devices.id"), nullable=False)
    action = Column(SAEnum(V2gAction), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    power_kw = Column(Float, default=7.2)
    energy_kwh = Column(Float, default=0.0)
    spot_price_per_kwh = Column(Float, default=0.0)
    deg_cost_per_kwh = Column(Float, default=0.0)
    projected_revenue_dollars = Column(Float, default=0.0)
    created_at = Column(DateTime, default=utcnow)

    device = relationship("Device", back_populates="v2g_schedules")


class Firmware(Base):
    __tablename__ = "firmware"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version = Column(String, nullable=False, unique=True)
    filename = Column(String, nullable=False)
    sha256_hash = Column(String, nullable=False)
    binary_path = Column(String, nullable=False)
    file_size = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)

    # Cryptographic signing (Feature 8)
    signature = Column(Text, nullable=True)        # Ed25519 signature hex
    signing_key_id = Column(String, nullable=True)  # public key identifier
    signed_by = Column(String, nullable=True)       # user/system that signed


class OtaDeployment(Base):
    __tablename__ = "ota_deployments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    firmware_id = Column(String, ForeignKey("firmware.id"), nullable=False)
    device_id = Column(String, ForeignKey("devices.id"), nullable=False)
    firmware_url = Column(String, nullable=True)
    status = Column(SAEnum(OtaStatus), default=OtaStatus.pending)
    retry_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    device = relationship("Device", back_populates="ota_deployments")
    firmware = relationship("Firmware")


class AlertStatus(str, enum.Enum):
    active = "active"
    acknowledged = "acknowledged"
    resolved = "resolved"


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    type = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    device_ids = Column(Text, default="")
    status = Column(SAEnum(AlertStatus), default=AlertStatus.active)
    dedup_key = Column(String, nullable=False)
    count = Column(Integer, default=1)
    channel = Column(String, default="")
    acknowledged_by = Column(String, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, nullable=False)
    name = Column(String, nullable=False)
    picture = Column(String, default="")
    login_time = Column(DateTime, default=utcnow)
    last_active = Column(DateTime, default=utcnow, onupdate=utcnow)
    revoked = Column(Integer, default=0)  # 0 = active, 1 = revoked
    role = Column(SAEnum(UserRole), default=UserRole.user)


# ── Feature 1: Telemetry time-series ──────────────────────────────────────────

class Telemetry(Base):
    __tablename__ = "telemetry"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String, ForeignKey("devices.id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    signal_strength = Column(Integer, nullable=True)
    uptime_percentage = Column(Float, nullable=True)
    soc = Column(Float, nullable=True)
    soh = Column(Float, nullable=True)
    battery_temp = Column(Float, nullable=True)
    plug_status = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    cpu_usage = Column(Float, nullable=True)
    memory_usage = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)

    device = relationship("Device", back_populates="telemetry")


# ── Feature 2: Geofencing ─────────────────────────────────────────────────────

class Geofence(Base):
    __tablename__ = "geofences"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    shape = Column(SAEnum(GeofenceShape), default=GeofenceShape.circle)
    # For circle: center_lat, center_lng, radius_meters
    center_lat = Column(Float, nullable=True)
    center_lng = Column(Float, nullable=True)
    radius_meters = Column(Float, nullable=True)
    # For polygon: GeoJSON-style coordinates JSON string
    polygon_coords = Column(Text, nullable=True)
    # Device assignment: empty = fleet-wide, comma-sep device IDs = specific
    device_ids = Column(Text, default="")
    alert_on_enter = Column(Boolean, default=True)
    alert_on_exit = Column(Boolean, default=True)
    color = Column(String, default="#2DD4BF")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class GeofenceEvent(Base):
    __tablename__ = "geofence_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    geofence_id = Column(String, ForeignKey("geofences.id"), nullable=False, index=True)
    device_id = Column(String, ForeignKey("devices.id"), nullable=False, index=True)
    event_type = Column(String, nullable=False)  # "enter" or "exit"
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    alerted = Column(Boolean, default=False)

    device = relationship("Device", back_populates="geofence_events")


# ── Feature 5: Offline command queue ──────────────────────────────────────────

class CommandQueue(Base):
    __tablename__ = "command_queue"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String, ForeignKey("devices.id"), nullable=False, index=True)
    command_type = Column(String, nullable=False)  # ota, config, v2g, restart, rollback
    payload = Column(Text, nullable=False)  # JSON payload to publish
    status = Column(SAEnum(CommandStatus), default=CommandStatus.queued, index=True)
    created_at = Column(DateTime, default=utcnow)
    delivered_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)


# ── Feature 6: Audit log ──────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    actor = Column(String, nullable=False)  # user email or "system"
    action = Column(String, nullable=False)  # e.g. "device.register", "ota.trigger"
    target_type = Column(String, nullable=True)  # "device", "firmware", "alert"
    target_id = Column(String, nullable=True)
    details = Column(Text, default="{}")  # JSON
    ip_address = Column(String, nullable=True)
    timestamp = Column(DateTime, default=utcnow, index=True)


# ── Feature 7: Device shadow / digital twin ───────────────────────────────────

class DeviceShadow(Base):
    __tablename__ = "device_shadows"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String, ForeignKey("devices.id"), nullable=False, index=True)
    state = Column(String, nullable=False)  # "desired" or "reported"
    payload = Column(Text, nullable=False)  # JSON state document
    version = Column(Integer, default=1)
    metadata_json = Column(Text, default="{}")
    timestamp = Column(DateTime, default=utcnow, index=True)

    device = relationship("Device", back_populates="shadows")


# ── Feature 4: Scheduled OTA / maintenance windows ────────────────────────────

class OtaSchedule(Base):
    __tablename__ = "ota_schedules"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    firmware_id = Column(String, ForeignKey("firmware.id"), nullable=False)
    device_ids = Column(Text, default="")  # comma-separated, empty = all
    all_devices = Column(Boolean, default=False)
    scheduled_for = Column(DateTime, nullable=False)
    blackout_start_hour = Column(Integer, nullable=True)  # e.g. 9 (9am)
    blackout_end_hour = Column(Integer, nullable=True)    # e.g. 17 (5pm)
    canary_percent = Column(Float, default=10.0)
    status = Column(SAEnum(ScheduleStatus), default=ScheduleStatus.scheduled, index=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    deployment_ids = Column(Text, default="")
    error_message = Column(Text, nullable=True)

    firmware = relationship("Firmware")


# ── Feature 3: Predictive maintenance ─────────────────────────────────────────

class PredictedFailure(Base):
    __tablename__ = "predicted_failures"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String, ForeignKey("devices.id"), nullable=False, index=True)
    risk_type = Column(String, nullable=False)  # signal_degradation, thermal, battery_degradation, intermittent
    risk_score = Column(Float, nullable=False)   # 0.0 - 1.0
    confidence = Column(Float, default=0.0)       # 0.0 - 1.0
    predicted_hours_to_failure = Column(Float, nullable=True)
    evidence = Column(Text, default="{}")        # JSON summary of telemetry trends
    recommendation = Column(Text, nullable=True)
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow, index=True)

    device = relationship("Device", back_populates="predicted_failures")


# ── Feature 11: Webhook / event stream ────────────────────────────────────────

class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    event_types = Column(Text, default="*")  # comma-separated or "*"
    secret = Column(String, nullable=True)   # HMAC signing secret
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)


class EventLog(Base):
    __tablename__ = "event_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(String, nullable=False, index=True)  # device.registered, ota.completed, etc.
    payload = Column(Text, nullable=False)  # JSON
    delivered = Column(Integer, default=0)  # count of successful webhook deliveries
    failed = Column(Integer, default=0)
    timestamp = Column(DateTime, default=utcnow, index=True)
