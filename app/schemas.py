from pydantic import BaseModel, Field, field_validator, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime
import json as _json

# Payload size caps: request bodies are parsed fully into memory, so bound the
# free-form JSON blobs to keep a single request from exhausting RAM.
_MAX_COMMAND_PAYLOAD_BYTES = 32 * 1024
_MAX_SHADOW_PAYLOAD_BYTES = 64 * 1024


def _json_size(value: Any) -> int:
    try:
        return len(_json.dumps(value).encode())
    except (TypeError, ValueError):
        return 0


class DeviceRegisterRequest(BaseModel):
    device_id: Optional[str] = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    firmware_version: str = Field(default="1.0.0", min_length=1, max_length=32)
    ip_address: str = Field(default="", max_length=64)
    mqtt_client_id: Optional[str] = Field(default=None, max_length=128)
    city: Optional[str] = Field(default=None, max_length=128)
    claim_token: Optional[str] = Field(default=None, max_length=128)
    vin: Optional[str] = Field(default=None, max_length=32)
    make: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=64)
    model_year: Optional[int] = Field(default=None, ge=1900, le=2100)


class DeviceRegisterResponse(BaseModel):
    device_id: str
    name: str
    firmware_version: str
    status: str
    mqtt_client_id: Optional[str] = None
    city: Optional[str] = None


class HeartbeatRequest(BaseModel):
    uptime_percentage: float = Field(default=100.0, ge=0.0, le=100.0)
    signal_strength: int = Field(default=0, ge=-120, le=10)
    soc: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    soh: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    battery_temp: Optional[float] = Field(default=None, ge=-60.0, le=120.0)
    plug_status: Optional[str] = Field(default=None, max_length=32)
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    city: Optional[str] = Field(default=None, max_length=128)
    cpu_usage: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    memory_usage: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    temperature: Optional[float] = Field(default=None, ge=-60.0, le=120.0)
    # ── MVP DATA-01: OBD-grade fields ──
    source: Optional[str] = Field(default="sim", max_length=16)
    dtc_codes: Optional[List[str]] = Field(default=None, max_length=64)
    fuel_level_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    odometer_km: Optional[float] = Field(default=None, ge=0.0)
    tire_pressures: Optional[Dict[str, float]] = None
    cell_voltages: Optional[List[float]] = Field(default=None, max_length=512)


class DeviceResponse(BaseModel):
    id: str
    name: str
    firmware_version: str
    status: str
    signal_strength: int
    last_seen: datetime
    uptime_percentage: float
    ip_address: str
    soc: Optional[float] = None
    soh: Optional[float] = None
    battery_temp: Optional[float] = None
    plug_status: str = "disconnected"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city: Optional[str] = None
    mqtt_client_id: Optional[str] = None
    previous_firmware_version: Optional[str] = None
    current_ota_id: Optional[str] = None
    vin: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[int] = None
    lifecycle_status: str = "active"
    decommissioned_at: Optional[datetime] = None
    decommissioned_by: Optional[str] = None
    decommissioned_reason: Optional[str] = None
    claim_token: Optional[str] = None

    model_config = {"from_attributes": True}


class DeviceListResponse(BaseModel):
    devices: List[DeviceResponse]
    total: int


class FirmwareUploadResponse(BaseModel):
    id: str
    version: str
    filename: str
    sha256_hash: str
    file_size: int
    created_at: datetime
    signature: Optional[str] = None
    signing_key_id: Optional[str] = None
    signed_by: Optional[str] = None

    model_config = {"from_attributes": True}


class OtaTriggerRequest(BaseModel):
    firmware_id: str = Field(min_length=1, max_length=128)
    device_ids: Optional[List[str]] = Field(default=None, max_length=1000)
    all_devices: bool = False
    # NOTE: the "either device_ids or all_devices" check intentionally stays in
    # the endpoint (400 + "Specify device_ids or set all_devices=true") rather
    # than a model_validator (which would surface as 422) — E2E pins the 400.


class OtaDeploymentResponse(BaseModel):
    id: str
    firmware_id: str
    device_id: str
    status: str
    retry_count: int
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OtaStatusResponse(BaseModel):
    deployments: List[OtaDeploymentResponse]
    total: int
    success_count: int
    failed_count: int
    in_progress_count: int


# ── V2G schemas ──────────────────────────────────────────────

class V2gDispatchSlot(BaseModel):
    start_time: str
    end_time: str
    action: str  # charge, discharge, idle
    power_kw: float
    energy_kwh: float
    spot_price_per_kwh: float
    deg_cost_per_kwh: float
    net_revenue_dollars: float


class V2gDispatchRequest(BaseModel):
    device_ids: Optional[List[str]] = Field(default=None, max_length=1000)
    all_devices: bool = False
    horizon_hours: int = Field(default=24, ge=1, le=168)


class V2gDispatchResponse(BaseModel):
    agent: str = "V2G Arbitrage Optimizer"
    type: str = "v2g_dispatch"
    summary: str
    total_projected_revenue_dollars: float
    total_deg_cost_dollars: float
    schedule: List[V2gDispatchSlot]
    devices_used: int


# ── Alert schemas ──────────────────────────────────────────────

class AlertResponse(BaseModel):
    id: str
    type: str
    severity: str
    message: str
    device_ids: str = ""
    status: str
    dedup_key: str
    count: int = 1
    channel: str = ""
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    work_order_id: Optional[str] = None

    model_config = {"from_attributes": True}


class AlertListResponse(BaseModel):
    alerts: List[AlertResponse]
    total: int


class AcknowledgeRequest(BaseModel):
    user: str


# ── MVP WO-01: work order schemas ─────────────────────────────────────────────

class WorkOrderCreateRequest(BaseModel):
    alert_id: Optional[str] = Field(default=None, max_length=128)
    device_ids: Optional[List[str]] = Field(default=None, max_length=1000)
    title: Optional[str] = Field(default=None, max_length=256)
    detail: str = Field(default="", max_length=8192)
    severity: str = Field(default="warning", max_length=32)
    assignee: Optional[str] = Field(default=None, max_length=256)
    cost_estimate: Optional[float] = Field(default=None, ge=0.0)


class WorkOrderCloseRequest(BaseModel):
    resolution: str = Field(default="", max_length=8192)
    parts_used: Optional[List[str]] = Field(default=None, max_length=128)
    cost: Optional[float] = Field(default=None, ge=0.0)


class WorkOrderResponse(BaseModel):
    id: str
    alert_id: Optional[str] = None
    device_ids: str = ""
    title: str
    detail: str = ""
    severity: str = "warning"
    status: str
    assignee: Optional[str] = None
    due_at: Optional[datetime] = None
    parts_json: str = "[]"
    cost_estimate: Optional[float] = None
    resolution: Optional[str] = None
    created_at: datetime
    closed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class WorkOrderListResponse(BaseModel):
    work_orders: List[WorkOrderResponse]
    total: int


# ── Feature 1: Telemetry schemas ──────────────────────────────────────────────

class TelemetryPoint(BaseModel):
    id: str
    device_id: str
    timestamp: datetime
    signal_strength: Optional[int] = None
    uptime_percentage: Optional[float] = None
    soc: Optional[float] = None
    soh: Optional[float] = None
    battery_temp: Optional[float] = None
    plug_status: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    temperature: Optional[float] = None
    source: Optional[str] = None
    dtc_codes: Optional[str] = None
    fuel_level_pct: Optional[float] = None
    odometer_km: Optional[float] = None
    tire_pressures: Optional[str] = None
    cell_min_v: Optional[float] = None
    cell_max_v: Optional[float] = None
    cell_spread_mv: Optional[float] = None

    model_config = {"from_attributes": True}


class TelemetrySeriesResponse(BaseModel):
    device_id: str
    points: List[TelemetryPoint]
    total: int


# ── Feature 2: Geofence schemas ───────────────────────────────────────────────

class GeofenceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    shape: str = Field(default="circle", max_length=16)
    center_lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    center_lng: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    radius_meters: Optional[float] = Field(default=None, gt=0.0, le=100000.0)
    polygon_coords: Optional[str] = Field(default=None, max_length=65536)
    device_ids: str = Field(default="", max_length=4096)
    alert_on_enter: bool = True
    alert_on_exit: bool = True
    color: str = Field(default="#2DD4BF", pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True


class GeofenceResponse(BaseModel):
    id: str
    name: str
    shape: str
    center_lat: Optional[float] = None
    center_lng: Optional[float] = None
    radius_meters: Optional[float] = None
    polygon_coords: Optional[str] = None
    device_ids: str = ""
    alert_on_enter: bool = True
    alert_on_exit: bool = True
    color: str = "#2DD4BF"
    enabled: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}


class GeofenceListResponse(BaseModel):
    geofences: List[GeofenceResponse]
    total: int


class GeofenceEventResponse(BaseModel):
    id: str
    geofence_id: str
    device_id: str
    event_type: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timestamp: datetime
    alerted: bool = False

    model_config = {"from_attributes": True}


# ── Feature 4: Scheduled OTA schemas ──────────────────────────────────────────

class OtaScheduleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    firmware_id: str = Field(min_length=1, max_length=128)
    device_ids: List[str] = Field(default_factory=list, max_length=1000)
    all_devices: bool = False
    scheduled_for: datetime
    blackout_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    blackout_end_hour: Optional[int] = Field(default=None, ge=0, le=23)
    canary_percent: float = Field(default=10.0, ge=0.0, le=100.0)


class OtaScheduleResponse(BaseModel):
    id: str
    name: str
    firmware_id: str
    device_ids: str = ""
    all_devices: bool = False
    scheduled_for: datetime
    blackout_start_hour: Optional[int] = None
    blackout_end_hour: Optional[int] = None
    canary_percent: float = 10.0
    status: str
    created_by: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    deployment_ids: str = ""
    error_message: Optional[str] = None

    model_config = {"from_attributes": True}


class OtaScheduleListResponse(BaseModel):
    schedules: List[OtaScheduleResponse]
    total: int


# ── Feature 5: Offline command queue schemas ──────────────────────────────────

class CommandQueueRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    command_type: str = Field(min_length=1, max_length=32)  # ota, config, v2g, restart, rollback
    payload: dict
    ttl_seconds: int = Field(default=86400, ge=60, le=604800)  # 1 min .. 7 days

    @field_validator("payload")
    @classmethod
    def _payload_size_cap(cls, value: dict) -> dict:
        if _json_size(value) > _MAX_COMMAND_PAYLOAD_BYTES:
            raise ValueError(
                f"payload exceeds {_MAX_COMMAND_PAYLOAD_BYTES} bytes serialized"
            )
        return value


class CommandQueueResponse(BaseModel):
    id: str
    device_id: str
    command_type: str
    payload: str
    status: str
    created_at: datetime
    delivered_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    retry_count: int = 0
    max_retries: int = 3

    model_config = {"from_attributes": True}


class CommandQueueListResponse(BaseModel):
    commands: List[CommandQueueResponse]
    total: int


# ── Feature 6: Audit log schemas ──────────────────────────────────────────────

class AuditLogResponse(BaseModel):
    id: str
    actor: str
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    details: str = "{}"
    ip_address: Optional[str] = None
    timestamp: datetime

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    logs: List[AuditLogResponse]
    total: int


# ── Feature 7: Device shadow schemas ──────────────────────────────────────────

class ShadowUpdateRequest(BaseModel):
    state: str = Field(default="desired", max_length=16)  # desired or reported
    payload: dict
    # ── MVP TWIN-01: optimistic-concurrency write ──
    # base_version: the latest version the writer saw. When set and stale,
    # the write is rejected with 409 + both versions instead of clobbering.
    base_version: Optional[int] = Field(default=None, ge=0)
    source: Optional[str] = Field(default="cloud", max_length=16)  # cloud | edge | device
    ttl_seconds: Optional[int] = Field(default=None, ge=0, le=2592000)  # edge snapshots may expire (≤30d)

    @field_validator("payload")
    @classmethod
    def _payload_size_cap(cls, value: dict) -> dict:
        if _json_size(value) > _MAX_SHADOW_PAYLOAD_BYTES:
            raise ValueError(
                f"payload exceeds {_MAX_SHADOW_PAYLOAD_BYTES} bytes serialized"
            )
        return value


class DeviceShadowResponse(BaseModel):
    id: str
    device_id: str
    state: str
    payload: str
    version: int
    metadata_json: str = "{}"
    timestamp: datetime
    supersedes_version: Optional[int] = None
    source: Optional[str] = "cloud"
    expires_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Feature 3: Predictive maintenance schemas ─────────────────────────────────

class PredictedFailureResponse(BaseModel):
    id: str
    device_id: str
    risk_type: str
    risk_score: float
    confidence: float = 0.0
    predicted_hours_to_failure: Optional[float] = None
    evidence: str = "{}"
    recommendation: Optional[str] = None
    resolved: bool = False
    created_at: datetime
    model_version: Optional[str] = "legacy"

    model_config = {"from_attributes": True}


class PredictedFailureListResponse(BaseModel):
    predictions: List[PredictedFailureResponse]
    total: int


# ── Feature 9: Device lifecycle schemas ───────────────────────────────────────

class DecommissionRequest(BaseModel):
    reason: str = Field(default="retired", max_length=512)
    factory_reset: bool = False
    actor: str = Field(default="system", max_length=256)


class ClaimDeviceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    claim_token: str = Field(min_length=1, max_length=128)
    firmware_version: str = Field(default="1.0.0", min_length=1, max_length=32)
    ip_address: str = Field(default="", max_length=64)
    mqtt_client_id: Optional[str] = Field(default=None, max_length=128)


# ── Feature 11: Webhook / event schemas ───────────────────────────────────────

class WebhookCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: HttpUrl
    event_types: str = Field(default="*", min_length=1, max_length=512)
    secret: Optional[str] = Field(default=None, max_length=256)
    enabled: bool = True


class WebhookResponse(BaseModel):
    id: str
    name: str
    url: str
    event_types: str = "*"
    secret: Optional[str] = None
    enabled: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}


class EventLogResponse(BaseModel):
    id: str
    event_type: str
    payload: str
    delivered: int = 0
    failed: int = 0
    timestamp: datetime

    model_config = {"from_attributes": True}


# ── Feature 13: Bulk import schemas ───────────────────────────────────────────

class BulkImportRow(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    firmware_version: str = Field(default="1.0.0", min_length=1, max_length=32)
    ip_address: str = Field(default="", max_length=64)
    mqtt_client_id: Optional[str] = Field(default=None, max_length=128)
    city: Optional[str] = Field(default=None, max_length=128)


class BulkImportResponse(BaseModel):
    imported: int
    skipped: int
    errors: List[str] = Field(default_factory=list)
    device_ids: List[str] = Field(default_factory=list)


# ── SRS Idea 4: Smart Cargo schemas ───────────────────────────────────────────

class CargoProfileUpdate(BaseModel):
    commodity: Optional[str] = Field(default=None, max_length=32)
    temp_min_c: Optional[float] = Field(default=None, ge=-80.0, le=80.0)
    temp_max_c: Optional[float] = Field(default=None, ge=-80.0, le=80.0)
    thermal_mass: Optional[float] = Field(default=None, gt=0.0, le=100.0)
    door_alerts: Optional[bool] = None
    trip_eta_minutes: Optional[float] = Field(default=None, ge=0.0, le=10080.0)


class CargoProfileResponse(BaseModel):
    device_id: str
    commodity: str = "general"
    temp_min_c: float = 2.0
    temp_max_c: float = 4.0
    thermal_mass: float = 1.0
    door_alerts: bool = True
    trip_eta_minutes: Optional[float] = None

    model_config = {"from_attributes": True}


class CargoReadingIngest(BaseModel):
    bay_temp_c: Optional[float] = Field(default=None, ge=-80.0, le=120.0)
    humidity_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    door_open: bool = False
    shock_g: Optional[float] = Field(default=None, ge=0.0, le=200.0)
    source: str = Field(default="sim", max_length=16)
    ai_inference: Optional[dict] = None

    @field_validator("ai_inference")
    @classmethod
    def _ai_inference_size_cap(cls, value: Optional[dict]) -> Optional[dict]:
        if value is not None and _json_size(value) > _MAX_SHADOW_PAYLOAD_BYTES:
            raise ValueError(
                f"ai_inference exceeds {_MAX_SHADOW_PAYLOAD_BYTES} bytes serialized"
            )
        return value


class CargoReadingResponse(BaseModel):
    id: str
    device_id: str
    timestamp: datetime
    bay_temp_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    door_open: bool = False
    shock_g: Optional[float] = None
    source: str = "sim"
    ai_inference: Optional[str] = None

    model_config = {"from_attributes": True}


class ShockEventResponse(BaseModel):
    id: str
    device_id: str
    timestamp: datetime
    peak_g: float
    axis: str = "z"
    event_class: str
    model_version: str = "heuristic-v1"

    model_config = {"from_attributes": True}
