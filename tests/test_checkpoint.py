import json
from datetime import datetime
from pathlib import Path

from health_api.core.checkpoint import CheckpointStore
from health_api.core.models import Fault, FileCheckpoint, Severity


def test_load_nonexistent(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "missing.json")
    assert store.load() == {}


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    store = CheckpointStore(state_file)

    fault = Fault(
        fault_id="F001",
        severity=Severity.ERROR,
        opened_at=datetime(2026, 10, 8, 9, 2, 0),
        message="Database unavailable",
    )
    checkpoint = FileCheckpoint(
        relative_path="dev/server-2.log",
        inode=12345,
        size_seen=1024,
        offset=512,
        active_faults={"F001": fault},
        seen_fault_ids={"F001"},
    )

    state = {"dev/server-2.log": checkpoint}
    assert store.save(state) is True
    assert state_file.exists()

    # Second save with identical state should return True quickly
    assert store.save(state) is True

    loaded = store.load()
    assert "dev/server-2.log" in loaded
    loaded_cp = loaded["dev/server-2.log"]
    assert loaded_cp.relative_path == "dev/server-2.log"
    assert loaded_cp.inode == 12345
    assert loaded_cp.size_seen == 1024
    assert loaded_cp.offset == 512
    assert "F001" in loaded_cp.active_faults
    assert loaded_cp.active_faults["F001"].severity is Severity.ERROR
    assert loaded_cp.active_faults["F001"].message == "Database unavailable"
    assert loaded_cp.seen_fault_ids == {"F001"}


def test_corrupt_json_quarantined(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{ broken json", encoding="utf-8")

    store = CheckpointStore(state_file)
    loaded = store.load()
    assert loaded == {}
    assert not state_file.exists()

    corrupt_files = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(corrupt_files) == 1
    assert corrupt_files[0].read_text(encoding="utf-8") == "{ broken json"


def test_schema_version_mismatch(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    data = {"version": 99, "files": {}}
    state_file.write_text(json.dumps(data), encoding="utf-8")

    store = CheckpointStore(state_file)
    assert store.load() == {}
    assert not state_file.exists()
    assert len(list(tmp_path.glob("state.json.corrupt-*"))) == 1


def test_path_traversal_quarantined(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    data = {
        "version": 1,
        "files": {
            "../escape.log": {
                "inode": 1,
                "size_seen": 10,
                "offset": 10,
                "active_faults": {},
                "seen_fault_ids": [],
            }
        },
    }
    state_file.write_text(json.dumps(data), encoding="utf-8")

    store = CheckpointStore(state_file)
    assert store.load() == {}
    assert len(list(tmp_path.glob("state.json.corrupt-*"))) == 1


def test_active_fault_missing_from_seen_ids(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    data = {
        "version": 1,
        "files": {
            "dev/server-1.log": {
                "inode": 1,
                "size_seen": 10,
                "offset": 10,
                "active_faults": {
                    "F001": {
                        "severity": "ERROR",
                        "opened_at": "2026-10-08T09:00:00",
                        "message": "broken",
                    }
                },
                "seen_fault_ids": [],  # missing F001
            }
        },
    }
    state_file.write_text(json.dumps(data), encoding="utf-8")

    store = CheckpointStore(state_file)
    assert store.load() == {}
    assert len(list(tmp_path.glob("state.json.corrupt-*"))) == 1


def test_invalid_severity_quarantined(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    data = {
        "version": 1,
        "files": {
            "dev/server-1.log": {
                "inode": 1,
                "size_seen": 10,
                "offset": 10,
                "active_faults": {
                    "F001": {
                        "severity": "UNKNOWN_SEV",
                        "opened_at": "2026-10-08T09:00:00",
                        "message": "broken",
                    }
                },
                "seen_fault_ids": ["F001"],
            }
        },
    }
    state_file.write_text(json.dumps(data), encoding="utf-8")

    store = CheckpointStore(state_file)
    assert store.load() == {}
    assert len(list(tmp_path.glob("state.json.corrupt-*"))) == 1
