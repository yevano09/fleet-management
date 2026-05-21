import uuid
from datetime import datetime, timezone

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)
from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import relationship
from app.database import Base
import enum


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


class Device(Base):
    __tablename__ = "devices"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    firmware_version = Column(String, default="1.0.0")
    status = Column(SAEnum(DeviceStatus), default=DeviceStatus.offline)
    signal_strength = Column(Integer, default=0)
    last_seen = Column(DateTime, default=_utcnow)
    uptime_percentage = Column(Float, default=100.0)
    ip_address = Column(String, default="")
    previous_firmware_version = Column(String, nullable=True)
    current_ota_id = Column(String, nullable=True)

    ota_deployments = relationship("OtaDeployment", back_populates="device")


class Firmware(Base):
    __tablename__ = "firmware"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version = Column(String, nullable=False, unique=True)
    filename = Column(String, nullable=False)
    sha256_hash = Column(String, nullable=False)
    binary_path = Column(String, nullable=False)
    file_size = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)


class OtaDeployment(Base):
    __tablename__ = "ota_deployments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    firmware_id = Column(String, ForeignKey("firmware.id"), nullable=False)
    device_id = Column(String, ForeignKey("devices.id"), nullable=False)
    status = Column(SAEnum(OtaStatus), default=OtaStatus.pending)
    retry_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    device = relationship("Device", back_populates="ota_deployments")
    firmware = relationship("Firmware")
