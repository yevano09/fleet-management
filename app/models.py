import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Text, Enum as SAEnum, Boolean, Index, UniqueConstraint
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


# ── P0 UC-26: Multi-tenancy ──────────────────────────────────────────────────
# Constants live in app.config (imported here for backwards compatibility).

DEFAULT_ORG_ID = "org-default"
SUPER_ORG = "*"


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=utcnow)


class ApiKey(Base):
    """Automation tokens (UC-23). Secret is stored as SHA-256 hash only;
    the raw `fck_...` secret is returned exactly once at creation."""

    __tablename__ = "api_keys"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)
    name = Column(String, nullable=False)
    prefix = Column(String, nullable=False)          # first chars of secret for identification
    key_hash = Column(String, nullable=False, index=True)  # sha256 hex of full secret
    role = Column(SAEnum(UserRole), default=UserRole.viewer)
    created_at = Column(DateTime, default=utcnow)
    revoked = Column(Integer, default=0)             # 0 = active, 1 = revoked


class DeviceCertificate(Base):
    """Device X.509 certificates issued by the internal CA (UC-25).
    Private keys are returned once at issue time and never persisted here."""

    __tablename__ = "device_certificates"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String, nullable=True, index=True)   # CN; null until JITP claim
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)
    fingerprint_sha256 = Column(String, nullable=False, unique=True, index=True)
    pem = Column(Text, nullable=False)               # public certificate only
    serial = Column(String, nullable=False)
    status = Column(String, default="active", index=True)   # issued|active|revoked|expired
    issued_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


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

    # Vehicle identity (OBD simulator + gateway: VIN/make/model from register)
    vin = Column(String, nullable=True, index=True)
    make = Column(String, nullable=True)
    model = Column(String, nullable=True)
    model_year = Column(Integer, nullable=True)

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

    # P0 UC-26: tenancy
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)

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

    # P0 UC-26: tenancy (firmware scoped per org)
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)


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


class WorkOrderStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    done = "done"
    cancelled = "cancelled"


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
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)
    # MVP WO-01: link to the auto/manual work order opened from this alert.
    # Plain string (no FK): avoids a circular alerts<->work_orders FK pair that
    # Postgres rejects at CREATE TABLE time. Integrity is enforced in code.
    work_order_id = Column(String, nullable=True)


# ── MVP WO-01: maintenance work orders ─────────────────────────────────────────

class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    alert_id = Column(String, ForeignKey("alerts.id"), nullable=True, index=True)
    device_ids = Column(Text, default="")
    title = Column(String, nullable=False)
    detail = Column(Text, default="")
    severity = Column(String, default="warning")
    status = Column(SAEnum(WorkOrderStatus), default=WorkOrderStatus.open)
    assignee = Column(String, nullable=True)
    due_at = Column(DateTime, nullable=True)
    parts_json = Column(Text, default="[]")
    cost_estimate = Column(Float, nullable=True)
    resolution = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    closed_at = Column(DateTime, nullable=True)
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)


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
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)


# ── Feature 1: Telemetry time-series ──────────────────────────────────────────

class Telemetry(Base):
    __tablename__ = "telemetry"
    # P-ret-1: id stays populated for backward-compat reads but is NOT the
    # physical PK on migrated DBs (dropped by 0002_telemetry_reshape for
    # hypertable readiness). Fresh create_all DBs still get id-PK; the P-ret-2
    # hypertable step drops it idempotently. Mapper needs a PK either way.
    __table_args__ = (
        UniqueConstraint("device_id", "timestamp", "source", name="telemetry_device_ts_source_unique"),
        Index("ix_telemetry_tenant_device_ts", "tenant_id", "device_id", "timestamp"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String, ForeignKey("devices.id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    # P-ret-1: hot-path tenancy stamped at ingest (no join inheritance at scale)
    tenant_id = Column(String, default=DEFAULT_ORG_ID, index=True)
    region = Column(String, default="default")
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
    # ── MVP DATA-01: OBD-II/edge-grade signal fields ──
    # source: sim | obd | oem | edge — which pipeline produced this point
    source = Column(String, default="sim")
    # dtc_codes: JSON list of diagnostic trouble codes, e.g. '["P0128"]'
    dtc_codes = Column(Text, nullable=True)
    fuel_level_pct = Column(Float, nullable=True)
    odometer_km = Column(Float, nullable=True)
    # tire_pressures: JSON dict, e.g. '{"fl": 32.1, "fr": 31.8, "rl": 32.0, "rr": 31.9}'
    tire_pressures = Column(Text, nullable=True)
    # Cell-voltage summary (BMS arrays summarized at ingest; raw arrays live
    # in P0-B feature views, not here)
    cell_min_v = Column(Float, nullable=True)
    cell_max_v = Column(Float, nullable=True)
    cell_spread_mv = Column(Float, nullable=True)

    device = relationship("Device", back_populates="telemetry")


# ── P-ret-1: 5-minute rollups (warm tier; raw older than hot_hours is dropped) ──

class TelemetryRollup5m(Base):
    __tablename__ = "telemetry_5m"

    device_id = Column(String, ForeignKey("devices.id"), nullable=False, primary_key=True)
    bucket = Column(DateTime, nullable=False, primary_key=True)
    tenant_id = Column(String, default=DEFAULT_ORG_ID, index=True)
    samples = Column(Integer, default=0)
    avg_signal = Column(Float, nullable=True)
    min_signal = Column(Float, nullable=True)
    avg_temp = Column(Float, nullable=True)
    max_temp = Column(Float, nullable=True)
    avg_soc = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("device_id", "bucket", name="telemetry_5m_device_bucket_unique"),
    )


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
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)


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
    # ── MVP TWIN-01: versioned twin writes ──
    # supersedes_version: the version this row replaced (chain head = latest).
    supersedes_version = Column(Integer, nullable=True)
    # source: cloud | edge | device — which pipeline wrote this row.
    source = Column(String, default="cloud")
    # expires_at: TTL for edge-reported snapshots (NULL = retain).
    expires_at = Column(DateTime, nullable=True)

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
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)

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
    # MVP ML-01: which scorer produced this row ("legacy" or a registry version)
    model_version = Column(String, default="legacy")

    device = relationship("Device", back_populates="predicted_failures")


# ── MVP ML-01: model registry ─────────────────────────────────────────────────

class MLModel(Base):
    __tablename__ = "ml_models"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version = Column(String, unique=True, nullable=False, index=True)
    artifact_path = Column(String, nullable=True)
    metrics_json = Column(Text, default="{}")
    stage = Column(String, default="staging")  # staging | production | archived
    trained_at = Column(DateTime, default=utcnow)


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
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)


class EventLog(Base):
    __tablename__ = "event_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(String, nullable=False, index=True)  # device.registered, ota.completed, etc.
    payload = Column(Text, nullable=False)  # JSON
    delivered = Column(Integer, default=0)  # count of successful webhook deliveries
    failed = Column(Integer, default=0)
    timestamp = Column(DateTime, default=utcnow, index=True)


# ── SRS Idea 4: Smart Cargo & Environmental Monitoring ─────────────────────────

class CargoProfile(Base):
    """Per-device cold-chain contract: thresholds + trip context for TTS."""
    __tablename__ = "cargo_profiles"

    device_id = Column(String, ForeignKey("devices.id"), nullable=False, primary_key=True)
    commodity = Column(String, default="general")  # pharma | dairy | produce | general
    temp_min_c = Column(Float, default=2.0)
    temp_max_c = Column(Float, default=4.0)
    thermal_mass = Column(Float, default=1.0)  # relative thermal inertia multiplier
    door_alerts = Column(Boolean, default=True)
    trip_eta_minutes = Column(Float, nullable=True)  # remaining trip time for TTS compare
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)


class CargoReading(Base):
    """Edge cargo telemetry: bay climate + door + shock summary + edge verdict."""
    __tablename__ = "cargo_readings"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String, ForeignKey("devices.id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    bay_temp_c = Column(Float, nullable=True)
    humidity_pct = Column(Float, nullable=True)
    door_open = Column(Boolean, default=False)
    shock_g = Column(Float, nullable=True)  # latest peak-g in window
    source = Column(String, default="sim")  # sim | edge | device
    # Edge inference block (TTS + last impact), stored verbatim as JSON.
    ai_inference = Column(Text, nullable=True)
    tenant_id = Column(String, default=DEFAULT_ORG_ID, index=True)

    __table_args__ = (
        Index("ix_cargo_device_ts", "device_id", "timestamp"),
    )


class ShockEvent(Base):
    """Classified handling events: NORMAL_ROAD_BUMP | CORNERING_FORCE | HARD_DROP | CARGO_COLLISION."""
    __tablename__ = "shock_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    device_id = Column(String, ForeignKey("devices.id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    peak_g = Column(Float, nullable=False)
    axis = Column(String, default="z")  # x | y | z | vector
    event_class = Column(String, nullable=False, index=True)
    model_version = Column(String, default="heuristic-v1")
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)


# ── SRS Idea 5: Fleet Agentic Copilot ──────────────────────────────────────────

class ServiceNote(Base):
    """RAG-lite knowledge base: SOPs, DTC guides, runbooks (FTS-ranked)."""
    __tablename__ = "service_notes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    source = Column(String, default="runbook")  # runbook | dtc | sop
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)


class CopilotSession(Base):
    __tablename__ = "copilot_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_email = Column(String, nullable=False, index=True)
    role = Column(String, default="operator")
    org_id = Column(String, ForeignKey("organizations.id"), default=DEFAULT_ORG_ID, index=True)
    created_at = Column(DateTime, default=utcnow)


class CopilotMessage(Base):
    """Only REDACTED content is stored; raw_sha tracks the original for forensics."""
    __tablename__ = "copilot_messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("copilot_sessions.id"), nullable=False, index=True)
    role = Column(String, nullable=False)  # user | assistant | tool
    content = Column(Text, nullable=False)  # redacted
    raw_sha = Column(String, nullable=True)
    tools_used = Column(Text, default="[]")  # JSON list
    provider = Column(String, default="mock")
    latency_ms = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)
