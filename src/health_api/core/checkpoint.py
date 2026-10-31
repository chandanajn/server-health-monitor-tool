"""Atomic JSON persistence for monitor checkpoints with quarantine on corruption."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from health_api.core.models import Fault, FileCheckpoint, Severity

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_STATE_BYTES = 64 * 1024 * 1024
MAX_MESSAGE_LENGTH = 4096
MAX_ID_LENGTH = 64
_INT_MAX = 2**64 - 1


class CheckpointError(ValueError):
    """Raised when checkpoint state document is invalid or unsafe."""


def _encode(state: dict[str, FileCheckpoint]) -> str:
    files = {
        rel: {
            "inode": cp.inode,
            "size_seen": cp.size_seen,
            "offset": cp.offset,
            "active_faults": {
                fault_id: {
                    "severity": fault.severity.name,
                    "opened_at": fault.opened_at.isoformat(),
                    "message": fault.message,
                }
                for fault_id, fault in cp.active_faults.items()
            },
            "seen_fault_ids": sorted(cp.seen_fault_ids),
        }
        for rel, cp in state.items()
    }
    document = {"version": SCHEMA_VERSION, "files": files}
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _as_dict(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckpointError(f"{where} must be an object")
    return value


def _exact(mapping: dict[str, Any], keys: set[str], where: str) -> None:
    if set(mapping) != keys:
        raise CheckpointError(f"{where} has missing or unexpected keys")


def _as_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckpointError(f"{where} must be an integer")
    if not 0 <= value <= _INT_MAX:
        raise CheckpointError(f"{where} is out of range")
    return value


def _as_str(value: object, where: str, max_length: int, min_length: int = 0) -> str:
    if not isinstance(value, str) or not min_length <= len(value) <= max_length:
        raise CheckpointError(f"{where} must be a string of valid length")
    return value


def _check_relative_path(rel: str) -> None:
    if not rel or "\x00" in rel:
        raise CheckpointError("empty or invalid file path in state")
    posix = PurePosixPath(rel)
    if (
        not posix.parts
        or posix.is_absolute()
        or ".." in posix.parts
        or posix.as_posix() != rel
        or PureWindowsPath(rel).drive
    ):
        raise CheckpointError(f"file path in state is not safely relative: {rel!r}")


def _decode_fault(fault_id: str, raw: object, where: str) -> Fault:
    entry = _as_dict(raw, where)
    _exact(entry, {"severity", "opened_at", "message"}, where)

    sev_name = _as_str(entry["severity"], f"{where} severity", 16, 1)
    try:
        severity = Severity.from_name(sev_name)
    except ValueError as exc:
        raise CheckpointError(f"{where} has invalid severity {sev_name!r}") from exc

    ts_raw = _as_str(entry["opened_at"], f"{where} opened_at", 64, 1)
    try:
        opened_at = datetime.fromisoformat(ts_raw)
    except ValueError as exc:
        raise CheckpointError(f"{where} has invalid opened_at timestamp {ts_raw!r}") from exc

    message = _as_str(entry["message"], f"{where} message", MAX_MESSAGE_LENGTH)
    return Fault(fault_id=fault_id, severity=severity, opened_at=opened_at, message=message)


def _decode_file(rel: str, raw: object) -> FileCheckpoint:
    _check_relative_path(rel)
    where = f"files[{rel!r}]"
    entry = _as_dict(raw, where)
    _exact(entry, {"inode", "size_seen", "offset", "active_faults", "seen_fault_ids"}, where)

    inode = _as_int(entry["inode"], f"{where} inode")
    size_seen = _as_int(entry["size_seen"], f"{where} size_seen")
    offset = _as_int(entry["offset"], f"{where} offset")

    seen_raw = entry["seen_fault_ids"]
    if not isinstance(seen_raw, list):
        raise CheckpointError(f"{where} seen_fault_ids must be a list")
    seen = {_as_str(item, f"{where} fault id", MAX_ID_LENGTH, 1) for item in seen_raw}

    faults: dict[str, Fault] = {}
    for fault_id, fault_raw in _as_dict(entry["active_faults"], where).items():
        _as_str(fault_id, f"{where} fault id", MAX_ID_LENGTH, 1)
        if fault_id not in seen:
            raise CheckpointError(f"{where} has an active fault missing from seen ids")
        faults[fault_id] = _decode_fault(fault_id, fault_raw, f"{where} fault")

    return FileCheckpoint(
        relative_path=rel,
        inode=inode,
        size_seen=size_seen,
        offset=offset,
        active_faults=faults,
        seen_fault_ids=seen,
    )


def _decode(document: object) -> dict[str, FileCheckpoint]:
    top = _as_dict(document, "state document")
    _exact(top, {"version", "files"}, "state document")
    if _as_int(top["version"], "version") != SCHEMA_VERSION:
        raise CheckpointError(f"unsupported state version {top['version']}")
    files = _as_dict(top["files"], "files")
    return {rel: _decode_file(rel, raw) for rel, raw in files.items()}


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class CheckpointStore:
    """Atomic load and save operations for monitor checkpoints."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._last_written: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, FileCheckpoint]:
        """Return state snapshot, or empty dict if missing, unreadable, or quarantined."""
        try:
            with open(self._path, "rb") as fh:
                raw = fh.read(MAX_STATE_BYTES + 1)
        except FileNotFoundError:
            logger.info("no state file at %s; starting with empty state", self._path)
            return {}
        except OSError as exc:
            logger.error("cannot read state file %s: %s; starting empty", self._path, exc)
            return {}

        try:
            if len(raw) > MAX_STATE_BYTES:
                raise CheckpointError("state file is too large")
            state = _decode(json.loads(raw.decode("utf-8")))
        except (ValueError, RecursionError, CheckpointError) as exc:
            self._quarantine(exc)
            return {}

        logger.info("restored state for %d server file(s)", len(state))
        return state

    def save(self, state: dict[str, FileCheckpoint]) -> bool:
        """Persist state atomically. Returns False on I/O failure."""
        text = _encode(state)
        if text == self._last_written and self._path.exists():
            return True

        tmp_name: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=self._path.parent,
                prefix=f"{self._path.name}.",
                suffix=".tmp",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self._path)
            tmp_name = None
        except OSError as exc:
            logger.error(
                "could not save state to %s: %s (previous checkpoint kept)",
                self._path,
                exc,
            )
            if tmp_name is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
            return False

        _fsync_directory(self._path.parent)
        self._last_written = text
        return True

    def _quarantine(self, reason: Exception) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = self._path.with_name(f"{self._path.name}.corrupt-{stamp}")
        try:
            os.replace(self._path, target)
        except OSError as exc:
            logger.error(
                "state file %s is invalid (%s) and could not be quarantined (%s); starting empty",
                self._path,
                reason,
                exc,
            )
            return
        logger.warning(
            "state file %s is invalid (%s); moved to %s; starting empty",
            self._path,
            reason,
            target.name,
        )
