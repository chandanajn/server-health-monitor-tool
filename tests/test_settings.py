from pathlib import Path

import pytest
from pydantic import ValidationError

from health_api.core.models import Severity
from health_api.settings import Settings


def test_default_settings() -> None:
    settings = Settings()
    assert settings.root == Path("org")
    assert settings.state_file == Path("state/state.json")
    assert settings.interval == 30.0
    assert settings.min_severity is Severity.ERROR
    assert settings.api_key is None
    assert settings.log_level == "INFO"
    assert settings.log_format == "text"
    assert settings.enable_docs is True
    assert settings.enable_metrics is True
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HM_INTERVAL", "15.5")
    monkeypatch.setenv("HM_MIN_SEVERITY", "WARN")
    monkeypatch.setenv("HM_API_KEY", "secure-api-key-at-least-16-chars")
    monkeypatch.setenv("HM_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("HM_LOG_FORMAT", "json")
    monkeypatch.setenv("HM_ENABLE_DOCS", "false")
    monkeypatch.setenv("HM_PORT", "9000")

    settings = Settings()
    assert settings.interval == 15.5
    assert settings.min_severity is Severity.WARN
    assert settings.api_key == "secure-api-key-at-least-16-chars"
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "json"
    assert settings.enable_docs is False
    assert settings.port == 9000


def test_dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("HM_INTERVAL=42.0\nHM_LOG_LEVEL=WARNING\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Ensure root exists in tmp_path
    (tmp_path / "org").mkdir()

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.interval == 42.0
    assert settings.log_level == "WARNING"


def test_real_env_beats_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("HM_INTERVAL=42.0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "org").mkdir()
    monkeypatch.setenv("HM_INTERVAL", "99.0")

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.interval == 99.0


def test_invalid_root_raises(tmp_path: Path) -> None:
    nonexistent = tmp_path / "does_not_exist"
    with pytest.raises(ValidationError) as exc:
        Settings(root=nonexistent)
    assert "HM_ROOT" in str(exc.value)


def test_invalid_interval_raises() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(interval=0.0)
    assert "HM_INTERVAL" in str(exc.value)


def test_invalid_min_severity_raises() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(min_severity="NOT_A_SEVERITY")  # type: ignore[arg-type]
    assert "HM_MIN_SEVERITY" in str(exc.value)


def test_short_api_key_raises() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(api_key="too-short")
    assert "HM_API_KEY" in str(exc.value)


def test_state_inside_root_rejected(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    root.mkdir()
    inside_state = root / "state.json"
    with pytest.raises(ValidationError) as exc:
        Settings(root=root, state_file=inside_state)
    assert "cannot be inside HM_ROOT" in str(exc.value)


def test_invalid_port_raises() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(port=99999)
    assert "HM_PORT" in str(exc.value)
