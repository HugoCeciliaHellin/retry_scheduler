from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, HttpUrl, field_serializer

from app.models import JobStatus


class CreateJobRequest(BaseModel):
    url: HttpUrl
    payload: dict[str, Any]


class CreateJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatus


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatus
    attempts: int
    next_retry_at: datetime | None

    @field_serializer("next_retry_at")
    def serialize_next_retry_at(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")