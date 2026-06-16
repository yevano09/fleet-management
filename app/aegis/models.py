import uuid
import json
from datetime import datetime

from sqlalchemy import Column, String, Integer, Float, DateTime, Text, Boolean

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


class RuleConfig(Base):
    __tablename__ = "rule_configs"

    rule_name = Column(String, primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    cooldown_seconds = Column(Integer, nullable=True)
    max_retries = Column(Integer, nullable=True)
    priority = Column(Integer, nullable=True)
    threshold_overrides = Column(Text, nullable=False, default="{}")

    def __init__(self, rule_name: str, enabled: bool = True,
                 cooldown_seconds: int = None, max_retries: int = None,
                 priority: int = None, threshold_overrides: str = "{}"):
        self.rule_name = rule_name
        self.enabled = enabled
        self.cooldown_seconds = cooldown_seconds
        self.max_retries = max_retries
        self.priority = priority
        self.threshold_overrides = threshold_overrides

    def get_threshold_overrides(self) -> dict:
        try:
            return json.loads(self.threshold_overrides or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
