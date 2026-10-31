"""Log line grammar parser for server log events and resolutions."""

from __future__ import annotations

import re
from datetime import datetime

from health_api.core.models import LogEvent, ParseError, Severity

MAX_LINE_LENGTH = 4096
PREVIEW_LENGTH = 80
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

_ID = r"[A-Za-z0-9_.-]{1,64}"

_LINE_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) +(?P<sev>[A-Za-z]+)(?: +(?P<rest>.*))?",
    re.ASCII,
)
_RESOLVE_RE = re.compile(rf"RESOLVED id=(?P<id>{_ID})", re.ASCII)
_TAG_RE = re.compile(rf"\[id=(?P<id>{_ID})\](?: +(?P<msg>.*))?", re.ASCII)


def _preview(text: str) -> str:
    """Return bounded printable characters safe for diagnostics."""
    shown = "".join(ch if ch.isprintable() else "?" for ch in text[:PREVIEW_LENGTH])
    return shown + "..." if len(text) > PREVIEW_LENGTH else shown


def _error(reason: str, text: str) -> ParseError:
    return ParseError(reason=reason, preview=_preview(text))


def parse_line(line: str) -> LogEvent | ParseError | None:
    """Parse a single line. Never raises exceptions on malformed text."""
    text = line.strip()
    if not text:
        return None
    if len(text) > MAX_LINE_LENGTH:
        return _error(f"line exceeds {MAX_LINE_LENGTH} characters", text)

    match = _LINE_RE.fullmatch(text)
    if match is None:
        return _error("expected '<YYYY-MM-DD HH:MM:SS> <SEVERITY> <message>'", text)

    try:
        timestamp = datetime.strptime(match["ts"], TIMESTAMP_FORMAT)
    except ValueError:
        return _error("invalid timestamp", text)

    try:
        severity = Severity.from_name(match["sev"])
    except ValueError:
        return _error(f"unknown severity {match['sev'][:16]!r}", text)

    rest = match["rest"] or ""

    if rest.split(" ", 1)[0] == "RESOLVED":
        return _parse_resolution(timestamp, severity, rest, text)

    if rest.startswith("[id="):
        tag = _TAG_RE.fullmatch(rest)
        if tag is None:
            return _error("invalid fault id (allowed: A-Z a-z 0-9 _ . -, max 64)", text)
        return LogEvent(
            timestamp=timestamp,
            severity=severity,
            message=tag["msg"] or "",
            fault_id=tag["id"],
        )

    return LogEvent(timestamp=timestamp, severity=severity, message=rest)


def _parse_resolution(
    timestamp: datetime,
    severity: Severity,
    rest: str,
    text: str,
) -> LogEvent | ParseError:
    if severity is not Severity.INFO:
        return _error("resolution lines must use INFO severity", text)
    match = _RESOLVE_RE.fullmatch(rest)
    if match is None:
        return _error("malformed resolution; expected 'RESOLVED id=<FAULT_ID>'", text)
    return LogEvent(
        timestamp=timestamp,
        severity=severity,
        message=rest,
        fault_id=match["id"],
        is_resolution=True,
    )
