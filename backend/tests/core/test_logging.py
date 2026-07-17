import structlog

from raghub.core.logging import configure_logging, redact_sensitive


def test_redaction_processor() -> None:
    event = redact_sensitive(None, "", {"password": "hunter2", "api_key": "sk-123", "msg": "ok"})
    assert event["password"] == "[REDACTED]"  # noqa: S105
    assert event["api_key"] == "[REDACTED]"
    assert event["msg"] == "ok"


def test_configure_logging_idempotent() -> None:
    configure_logging()
    configure_logging()
    assert structlog.is_configured()
