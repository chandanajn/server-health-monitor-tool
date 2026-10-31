"""Logging configuration supporting readable text and structured JSON output."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class RequestIdFilter(logging.Filter):
    """Injects current request_id from contextvars into each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """Outputs single-line JSON log entries."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Configure root logging to stderr with request ID enrichment."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdFilter())

    if fmt.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        text_fmt = "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s"
        handler.setFormatter(logging.Formatter(text_fmt, datefmt="%Y-%m-%d %H:%M:%S"))

    root.addHandler(handler)

