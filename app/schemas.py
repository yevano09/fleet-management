from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class DeviceRegisterRequest(BaseModel):
    device_id: Optional[str] = None
    name: str
    firmware_version: str = "1.0.0"
    ip_address: str = ""
    mqtt_client_id: Optional[str] = None


class DeviceRegisterResponse(BaseModel):
    device_id: str
    name: str
    firmware_version: str
    status: str
    mqtt_client_id: Optional[str] = None


class HeartbeatRequest(BaseModel):
    uptime_percentage: float = 100.0
    signal_strength: int = 0
    soc: Optional[float] = None
    soh: Optional[float] = None
    battery_temp: Optional[float] = None
    plug_status: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


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
