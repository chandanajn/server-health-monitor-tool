from datetime import datetime

import pytest

from health_api.core.models import Fault, LogEvent, ParseError, ScanReport, ServerStatus, Severity
from health_api.core.parser import parse_line


def test_severity_from_name() -> None:
    assert Severity.from_name("info") is Severity.INFO
    assert Severity.from_name("WARN") is Severity.WARN
    assert Severity.from_name(" error ") is Severity.ERROR
    assert Severity.from_name("crit") is Severity.CRIT

    with pytest.raises(ValueError, match="Unknown severity"):
        Severity.from_name("INVALID")


def test_parse_valid_event_without_id() -> None:
    line = "2026-10-08 09:00:00 INFO Service started normally"
    event = parse_line(line)
    assert isinstance(event, LogEvent)
    assert event.timestamp == datetime(2026, 10, 8, 9, 0, 0)
    assert event.severity is Severity.INFO
    assert event.message == "Service started normally"
    assert event.fault_id is None
    assert event.is_resolution is False
    assert event.triggers_fault(Severity.ERROR) is False


def test_parse_valid_event_with_id() -> None:
    line = "2026-10-08 09:02:00 ERROR [id=F001] Database connection lost"
    event = parse_line(line)
    assert isinstance(event, LogEvent)
    assert event.severity is Severity.ERROR
    assert event.fault_id == "F001"
    assert event.message == "Database connection lost"
    assert event.triggers_fault(Severity.ERROR) is True
    assert event.triggers_fault(Severity.CRIT) is False


def test_parse_valid_resolution() -> None:
    line = "2026-10-08 09:10:00 INFO RESOLVED id=F001"
    event = parse_line(line)
    assert isinstance(event, LogEvent)
    assert event.severity is Severity.INFO
    assert event.fault_id == "F001"
    assert event.is_resolution is True
    assert event.triggers_fault(Severity.INFO) is False


def test_parse_resolution_wrong_severity() -> None:
    line = "2026-10-08 09:10:00 ERROR RESOLVED id=F001"
    err = parse_line(line)
    assert isinstance(err, ParseError)
    assert "resolution lines must use INFO" in err.reason


def test_parse_resolution_malformed() -> None:
    line = "2026-10-08 09:10:00 INFO RESOLVED F001"
    err = parse_line(line)
    assert isinstance(err, ParseError)
    assert "malformed resolution" in err.reason


def test_parse_blank_or_whitespace() -> None:
    assert parse_line("") is None
    assert parse_line("   \n\t  ") is None


def test_parse_oversized_line() -> None:
    line = "2026-10-08 09:00:00 INFO " + ("A" * 5000)
    err = parse_line(line)
    assert isinstance(err, ParseError)
    assert "line exceeds 4096 characters" in err.reason


def test_parse_malformed_timestamp() -> None:
    line = "2026-99-99 99:99:99 ERROR Something broke"
    err = parse_line(line)
    assert isinstance(err, ParseError)
    assert "invalid timestamp" in err.reason


def test_parse_invalid_layout() -> None:
    err = parse_line("Not a valid log record at all")
    assert isinstance(err, ParseError)
    assert "expected '<YYYY-MM-DD" in err.reason


def test_parse_unknown_severity() -> None:
    line = "2026-10-08 09:00:00 PANIC Kernel panic"
    err = parse_line(line)
    assert isinstance(err, ParseError)
    assert "unknown severity" in err.reason


def test_parse_bad_fault_id_characters() -> None:
    line = "2026-10-08 09:00:00 ERROR [id=bad fault id!] Message"
    err = parse_line(line)
    assert isinstance(err, ParseError)
    assert "invalid fault id" in err.reason


def test_parse_bad_fault_id_too_long() -> None:
    long_id = "A" * 65
    line = f"2026-10-08 09:00:00 ERROR [id={long_id}] Message"
    err = parse_line(line)
    assert isinstance(err, ParseError)
    assert "invalid fault id" in err.reason


def test_preview_sanitizes_control_characters() -> None:
    err = parse_line("invalid layout with \x00\x08 non printable")
    assert isinstance(err, ParseError)
    assert "?" in err.preview
    assert "\x00" not in err.preview


def test_server_status_properties() -> None:
    fault = Fault(
        fault_id="F001",
        severity=Severity.ERROR,
        opened_at=datetime(2026, 10, 8, 9, 0, 0),
        message="Failure",
    )
    status = ServerStatus(
        relative_path="prd/server-6.log",
        active_faults=(fault,),
    )
    assert status.is_faulty is True
    assert status.env == "prd"
    assert status.environment == "prd"
    assert status.name == "server-6"
    assert status.highest_severity is Severity.ERROR

    healthy = ServerStatus(relative_path="dev/server-1.log")
    assert healthy.is_faulty is False
    assert healthy.highest_severity is None


def test_scan_report_defaults() -> None:
    report = ScanReport(total_servers=1, faulty_servers=0)
    assert report.total_servers == 1
    assert report.faulty_servers == 0
    assert report.scanned_at is not None
    assert report.duration_ms == 0.0
