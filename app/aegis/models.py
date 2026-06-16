import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, DateTime, Text

from app.database import Base
from app.utils import utcnow


class Remediation(Base):
    __tablename__ = "remediations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    signal_id = Column(String, nullable=False, index=True)
    metric_name = Column(String, nullable=False)
    value = Column(Float, nullable=False)
    threshold = Column(Float, nullable=False)
    severity = Column(String, nullable=False)
    rule_name = Column(String, nullable=True)
    action_name = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending", index=True)
    input_snapshot = Column(Text, default="{}")
    output_snapshot = Column(Text, default="{}")
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    retry_count = Column(Integer, default=0)
    device_ids = Column(Text, default="")
