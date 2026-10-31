"""Domain models for health monitoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import PurePosixPath


class Severity(IntEnum):
    """Log severities with integer precedence for filtering."""

    INFO = 10
    WARN = 20
    ERROR = 30
    CRIT = 40

    @classmethod
    def from_name(cls, name: str) -> Severity:
        """Parse severity name case-insensitively."""
        try:
            return cls[name.strip().upper()]
        except (KeyError, AttributeError):
            valid = ", ".join(m.name for m in cls)
            raise ValueError(
                f"Unknown severity {str(name)[:16]!r}; expected one of: {valid}"
            ) from None


@dataclass(frozen=True, slots=True)
class LogEvent:
    """Parsed log event record."""

    timestamp: datetime
    severity: Severity
    message: str
    fault_id: str | None = None
    is_resolution: bool = False

    def triggers_fault(self, threshold: Severity) -> bool:
        """Check if this event triggers a persistent fault at given threshold."""
        return not self.is_resolution and self.severity >= threshold


@dataclass(frozen=True, slots=True)
class ParseError:
    """Rejected log line with reason and bounded preview."""

    reason: str
    preview: str


@dataclass(frozen=True, slots=True)
class Fault:
    """Active unresolved fault on a server."""

    fault_id: str
    severity: Severity
    opened_at: datetime
    message: str


@dataclass(slots=True)
class FileCheckpoint:
    """Per-file state persisted between scans."""

    relative_path: str
    inode: int
    size_seen: int = 0
    offset: int = 0
    active_faults: dict[str, Fault] = field(default_factory=dict)
    seen_fault_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class ServerStatus:
    """Monitored status for a single server log."""

    relative_path: str
    active_faults: tuple[Fault, ...] = ()
    readable: bool = True
    error: str | None = None

    @property
    def is_faulty(self) -> bool:
        return bool(self.active_faults)

    @property
    def env(self) -> str:
        """Environment directory name extracted from relative path."""
        parts = PurePosixPath(self.relative_path.replace("\\", "/")).parts
        return parts[0] if len(parts) > 1 else ""

    @property
    def environment(self) -> str:
        """Alias for env."""
        return self.env

    @property
    def name(self) -> str:
        """Server identifier stem extracted from filename."""
        return PurePosixPath(self.relative_path.replace("\\", "/")).stem

    @property
    def highest_severity(self) -> Severity | None:
        """Highest active fault severity, or None if healthy."""
        if not self.active_faults:
            return None
        return max(f.severity for f in self.active_faults)


@dataclass(frozen=True, slots=True)
class ScanReport:
    """Aggregate report from a single scan cycle."""

    total_servers: int
    faulty_servers: int
    statuses: tuple[ServerStatus, ...] = ()
    discovery_errors: tuple[str, ...] = ()
    scanned_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float = 0.0
