import json
import logging

from health_api.logging_config import (
    JsonFormatter,
    RequestIdFilter,
    configure_logging,
    request_id_ctx,
)


def test_json_formatter_structure() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test message with %s",
        args=("arg1",),
        exc_info=None,
    )
    record.request_id = "req-12345"

    output = formatter.format(record)
    data = json.loads(output)

    assert data["level"] == "INFO"
    assert data["logger"] == "test_logger"
    assert data["message"] == "Test message with arg1"
    assert data["request_id"] == "req-12345"
    assert "timestamp" in data


def test_request_id_filter() -> None:
    flt = RequestIdFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    # Without contextvar set, defaults to "-"
    flt.filter(record)
    assert getattr(record, "request_id", None) == "-"

    # With contextvar set
    token = request_id_ctx.set("trace-abc")
    try:
        flt.filter(record)
        assert getattr(record, "request_id", None) == "trace-abc"
    finally:
        request_id_ctx.reset(token)


def test_configure_logging() -> None:
    configure_logging(level="DEBUG", fmt="json")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)

    configure_logging(level="INFO", fmt="text")
    assert root.level == logging.INFO
    assert len(root.handlers) == 1
    assert not isinstance(root.handlers[0].formatter, JsonFormatter)
