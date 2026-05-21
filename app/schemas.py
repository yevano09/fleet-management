from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class DeviceRegisterRequest(BaseModel):
    device_id: Optional[str] = None
    name: str
    firmware_version: str = "1.0.0"
    ip_address: str = ""


class DeviceRegisterResponse(BaseModel):
    device_id: str
    name: str
    firmware_version: str
    status: str


class HeartbeatRequest(BaseModel):
    uptime_percentage: float = 100.0
    signal_strength: int = 0


class DeviceResponse(BaseModel):
    id: str
    name: str
    firmware_version: str
    status: str
    signal_strength: int
    last_seen: datetime
    uptime_percentage: float
    ip_address: str

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
