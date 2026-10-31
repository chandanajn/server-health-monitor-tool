"""Incremental log monitoring engine with rotation detection and state persistence."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from health_api.core.models import (
    Fault,
    FileCheckpoint,
    LogEvent,
    ParseError,
    ScanReport,
    ServerStatus,
    Severity,
)
from health_api.core.parser import MAX_LINE_LENGTH, parse_line

logger = logging.getLogger(__name__)

MAX_LINE_BYTES = MAX_LINE_LENGTH * 4
MAX_DIAGNOSTICS_PER_FILE = 20


@dataclass(frozen=True, slots=True)
class MonitorSettings:
    """Core monitor engine settings."""

    root: Path | str
    min_severity: Severity = Severity.ERROR


def _describe(exc: OSError) -> str:
    return f"{type(exc).__name__}: {exc.strerror or 'error'}"


def _iter_complete_lines(fh: BinaryIO, start: int) -> Iterator[tuple[bytes | None, int, int]]:
    """Yield (line_bytes, start_offset, end_offset) for complete lines ending with newline."""
    pos = start
    while True:
        chunk = fh.readline(MAX_LINE_BYTES + 1)
        if not chunk:
            return
        length = len(chunk)
        oversized = False
        if not chunk.endswith(b"\n"):
            if length <= MAX_LINE_BYTES:
                return
            oversized = True
            while True:
                rest = fh.readline(MAX_LINE_BYTES)
                if not rest:
                    return
                length += len(rest)
                if rest.endswith(b"\n"):
                    break
        yield (None if oversized else chunk), pos, pos + length
        pos += length


class Monitor:
    """Incremental log monitor tracking server files and unresolved faults."""

    def __init__(
        self,
        settings: MonitorSettings | object,
        state: dict[str, FileCheckpoint] | None = None,
    ) -> None:
        if hasattr(settings, "root") and hasattr(settings, "min_severity"):
            self._root = Path(settings.root)
            self._min_severity = settings.min_severity
        else:
            raise TypeError("settings must provide root and min_severity attributes")
        self._state: dict[str, FileCheckpoint] = {} if state is None else state

    @property
    def state(self) -> dict[str, FileCheckpoint]:
        return self._state

    def scan_once(self) -> ScanReport:
        """Execute a single scan cycle over the root directory."""
        start_time = time.perf_counter()
        files, errors, dirs_ok = self._discover()
        statuses: list[ServerStatus] = []
        seen: set[str] = set()

        for rel, path in files:
            seen.add(rel)
            err = self._process_file(rel, path)
            cp = self._state.get(rel)
            readable = err is None
            if not readable:
                errors.append(f"{rel}: {err}")
            statuses.append(self._status(rel, cp, readable=readable, error=err))

        # Check remembered files not discovered on this cycle
        for rel in sorted(set(self._state) - seen):
            cp = self._state[rel]
            if cp.active_faults:
                err = "file missing but has unresolved active faults"
                statuses.append(self._status(rel, cp, readable=False, error=err))
            elif dirs_ok:
                del self._state[rel]

        statuses.sort(key=lambda s: s.relative_path)
        faulty_count = sum(1 for s in statuses if s.is_faulty)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return ScanReport(
            total_servers=len(statuses),
            faulty_servers=faulty_count,
            statuses=tuple(statuses),
            discovery_errors=tuple(errors),
            scanned_at=datetime.now(UTC),
            duration_ms=duration_ms,
        )

    def _discover(self) -> tuple[list[tuple[str, str]], list[str], bool]:
        root = self._root.resolve()
        if not root.is_dir():
            return [], [f"{self._root}: directory not found"], False

        files: list[tuple[str, str]] = []
        errors: list[str] = []
        dirs_ok = True
        stack: list[Path] = [root]

        while stack:
            cur = stack.pop()
            try:
                entries = list(os.scandir(cur))
            except OSError as exc:
                dirs_ok = False
                errors.append(f"{cur.name}: cannot read directory ({_describe(exc)})")
                continue

            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_symlink():
                        logger.debug("skipping symlink %r", entry.name)
                    elif entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        rel = Path(entry.path).relative_to(root).as_posix()
                        files.append((rel, entry.path))
                except OSError as exc:
                    dirs_ok = False
                    errors.append(f"{entry.name}: cannot inspect ({_describe(exc)})")

        files.sort(key=lambda item: item[0])
        return files, errors, dirs_ok

    def _process_file(self, rel: str, path: str) -> str | None:
        try:
            st = os.stat(path)
        except OSError as exc:
            return _describe(exc)

        cp = self._state.get(rel)
        if cp is None:
            cp = FileCheckpoint(relative_path=rel, inode=st.st_ino)
            self._state[rel] = cp
        elif cp.inode != st.st_ino or st.st_size < cp.offset:
            logger.warning("file %r replaced or truncated; re-reading from start", rel)
            cp.inode = st.st_ino
            cp.offset = 0
            cp.size_seen = 0

        if st.st_size == cp.size_seen:
            return None

        try:
            self._read_new_lines(cp, path)
        except OSError as exc:
            return _describe(exc)
        return None

    def _read_new_lines(self, cp: FileCheckpoint, path: str) -> None:
        problems = 0
        with open(path, "rb") as fh:
            fh.seek(cp.offset)
            for raw, start, stop in _iter_complete_lines(fh, cp.offset):
                if raw is None:
                    problems += 1
                    self._note_problem(cp, start, f"line exceeds {MAX_LINE_BYTES} bytes", problems)
                else:
                    event = parse_line(raw.decode("utf-8", errors="replace"))
                    if isinstance(event, ParseError):
                        problems += 1
                        self._note_problem(cp, start, event.reason, problems, event.preview)
                    elif event is not None:
                        self._apply(cp, event, start)
                cp.offset = stop
                cp.size_seen = max(cp.size_seen, stop)
            cp.size_seen = fh.tell()

        if problems > MAX_DIAGNOSTICS_PER_FILE:
            logger.warning(
                "%d further malformed lines in %r suppressed",
                problems - MAX_DIAGNOSTICS_PER_FILE,
                cp.relative_path,
            )

    @staticmethod
    def _note_problem(
        cp: FileCheckpoint,
        offset: int,
        reason: str,
        count: int,
        preview: str = "",
    ) -> None:
        if count <= MAX_DIAGNOSTICS_PER_FILE:
            logger.warning("malformed line in %r at byte %d: %s", cp.relative_path, offset, reason)
            if preview:
                logger.debug("malformed line preview: %r", preview)

    def _apply(self, cp: FileCheckpoint, event: LogEvent, line_start: int) -> None:
        if event.is_resolution:
            if cp.active_faults.pop(event.fault_id or "", None) is None:
                logger.info(
                    "resolution for unknown or inactive fault %r in %r ignored",
                    event.fault_id,
                    cp.relative_path,
                )
            return

        if not event.triggers_fault(self._min_severity):
            return

        fault_id = event.fault_id or f"auto-{line_start}"
        if fault_id in cp.seen_fault_ids:
            return
        cp.seen_fault_ids.add(fault_id)
        cp.active_faults[fault_id] = Fault(
            fault_id=fault_id,
            severity=event.severity,
            opened_at=event.timestamp,
            message=event.message,
        )

    @staticmethod
    def _status(
        rel: str,
        cp: FileCheckpoint | None,
        readable: bool,
        error: str | None,
    ) -> ServerStatus:
        faults: tuple[Fault, ...] = ()
        if cp is not None and cp.active_faults:
            faults = tuple(
                sorted(cp.active_faults.values(), key=lambda f: (f.opened_at, f.fault_id))
            )
        return ServerStatus(relative_path=rel, active_faults=faults, readable=readable, error=error)
