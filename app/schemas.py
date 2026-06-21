from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class DeviceRegisterRequest(BaseModel):
    device_id: Optional[str] = None
    name: str
    firmware_version: str = "1.0.0"
    ip_address: str = ""
    mqtt_client_id: Optional[str] = None
    city: Optional[str] = None
    claim_token: Optional[str] = None


class DeviceRegisterResponse(BaseModel):
    device_id: str
    name: str
    firmware_version: str
    status: str
    mqtt_client_id: Optional[str] = None
    city: Optional[str] = None


class HeartbeatRequest(BaseModel):
    uptime_percentage: float = 100.0
    signal_strength: int = 0
    soc: Optional[float] = None
    soh: Optional[float] = None
    battery_temp: Optional[float] = None
    plug_status: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city: Optional[str] = None
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    temperature: Optional[float] = None


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
    firmware_id: str
    device_ids: Optional[List[str]] = None
    all_devices: bool = False


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
    device_ids: Optional[List[str]] = None
    all_devices: bool = False
    horizon_hours: int = 24


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

    model_config = {"from_attributes": True}


class AlertListResponse(BaseModel):
    alerts: List[AlertResponse]
    total: int


class AcknowledgeRequest(BaseModel):
    user: str


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

    model_config = {"from_attributes": True}


class TelemetrySeriesResponse(BaseModel):
    device_id: str
    points: List[TelemetryPoint]
    total: int


# ── Feature 2: Geofence schemas ───────────────────────────────────────────────

class GeofenceCreateRequest(BaseModel):
    name: str
    shape: str = "circle"
    center_lat: Optional[float] = None
    center_lng: Optional[float] = None
    radius_meters: Optional[float] = None
    polygon_coords: Optional[str] = None
    device_ids: str = ""
    alert_on_enter: bool = True
    alert_on_exit: bool = True
    color: str = "#2DD4BF"
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
    name: str
    firmware_id: str
    device_ids: List[str] = []
    all_devices: bool = False
    scheduled_for: datetime
    blackout_start_hour: Optional[int] = None
    blackout_end_hour: Optional[int] = None
    canary_percent: float = 10.0


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
    device_id: str
    command_type: str  # ota, config, v2g, restart, rollback
    payload: dict
    ttl_seconds: int = 86400


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
    state: str = "desired"  # desired or reported
    payload: dict


class DeviceShadowResponse(BaseModel):
    id: str
    device_id: str
    state: str
    payload: str
    version: int
    metadata_json: str = "{}"
    timestamp: datetime

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

    model_config = {"from_attributes": True}


class PredictedFailureListResponse(BaseModel):
    predictions: List[PredictedFailureResponse]
    total: int


# ── Feature 9: Device lifecycle schemas ───────────────────────────────────────

class DecommissionRequest(BaseModel):
    reason: str = "retired"
    factory_reset: bool = False
    actor: str = "system"


class ClaimDeviceRequest(BaseModel):
    name: str
    claim_token: str
    firmware_version: str = "1.0.0"
    ip_address: str = ""
    mqtt_client_id: Optional[str] = None


# ── Feature 11: Webhook / event schemas ───────────────────────────────────────

class WebhookCreateRequest(BaseModel):
    name: str
    url: str
    event_types: str = "*"
    secret: Optional[str] = None
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
    name: str
    firmware_version: str = "1.0.0"
    ip_address: str = ""
    mqtt_client_id: Optional[str] = None
    city: Optional[str] = None


class BulkImportResponse(BaseModel):
    imported: int
    skipped: int
    errors: List[str] = []
    device_ids: List[str] = []
