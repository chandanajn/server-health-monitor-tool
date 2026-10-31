"""Pydantic request and response schemas for REST API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

MAX_RESPONSE_MESSAGE_LENGTH = 500


def sanitize_message(text: str) -> str:
    """Strip control characters and truncate untrusted log messages."""
    clean = "".join(ch for ch in text if ch.isprintable())
    return clean[:MAX_RESPONSE_MESSAGE_LENGTH]


class FaultOut(BaseModel):
    fault_id: str
    severity: str
    since: datetime
    message: str


class FlatFaultOut(BaseModel):
    server: str
    env: str
    fault_id: str
    severity: str
    since: datetime
    message: str


class AffectedServerOut(BaseModel):
    server: str
    env: str
    name: str
    highest_severity: str | None
    active_faults: list[FaultOut]


class ReportOut(BaseModel):
    generated_at: datetime
    last_scan_at: datetime | None
    min_severity: str
    total_servers: int
    faulty_servers: int
    affected_servers: list[AffectedServerOut]


class ServerOut(BaseModel):
    server: str
    env: str
    name: str
    is_faulty: bool
    fault_count: int
    highest_severity: str | None
    readable: bool


class ServerDetailOut(BaseModel):
    server: str
    env: str
    name: str
    is_faulty: bool
    highest_severity: str | None
    readable: bool
    error: str | None
    active_faults: list[FaultOut]


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
