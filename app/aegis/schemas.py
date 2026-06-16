from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class RemediationSignal(BaseModel):
    id: str
    metric_name: str
    value: float
    threshold: float
    severity: str
    timestamp: datetime
    device_ids: List[str] = []
    window_seconds: int = 60
    metadata: dict = {}


class RemediationResponse(BaseModel):
    id: str
    signal_id: str
    metric_name: str
    value: float
    threshold: float
    severity: str
    rule_name: Optional[str] = None
    action_name: Optional[str] = None
    status: str
    input_snapshot: str = "{}"
    output_snapshot: str = "{}"
    error_message: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    retry_count: int = 0
    device_ids: str = ""

    model_config = {"from_attributes": True}


class RemediationListResponse(BaseModel):
    remediations: List[RemediationResponse]
    total: int
    limit: int
    offset: int


class IngestRequest(BaseModel):
    metric_name: str
    value: float
    threshold: float = 0.0
    severity: str = "warning"
    device_ids: List[str] = []
    window_seconds: int = 60
    metadata: dict = {}
