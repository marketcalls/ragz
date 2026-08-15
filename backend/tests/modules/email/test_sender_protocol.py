from pathlib import Path

import pytest
from pydantic import ValidationError

from ragz.core.config import Settings
from ragz.core.errors import RagzError
from ragz.modules.email.errors import EmailError
from ragz.modules.email.schemas import EmailConfig, EmailMessage
from ragz.modules.email.sender import EmailSender
from ragz.modules.models import settings_service
from ragz.modules.secrets.crypto import ensure_kek


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


# --- EmailMessage -----------------------------------------------------------


def test_email_message_validates_with_valid_fields() -> None:
    msg = EmailMessage(to="user@example.com", subject="Hi", html="<p>hi</p>", text="hi")
    assert msg.to == "user@example.com"
    assert msg.subject == "Hi"


def test_email_message_rejects_over_limit_subject() -> None:
    with pytest.raises(ValidationError):
        EmailMessage(to="user@example.com", subject="x" * 501, html="<p>hi</p>", text="hi")


def test_email_message_rejects_over_limit_to() -> None:
    with pytest.raises(ValidationError):
        EmailMessage(to="x" * 321, subject="Hi", html="<p>hi</p>", text="hi")


def test_email_message_rejects_empty_required_fields() -> None:
    with pytest.raises(ValidationError):
        EmailMessage(to="", subject="Hi", html="<p>hi</p>", text="hi")


# --- EmailSender protocol ----------------------------------------------------


class _InMemorySender:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


def test_in_memory_sender_satisfies_protocol() -> None:
    sender: EmailSender = _InMemorySender()
    assert hasattr(sender, "send")


async def test_in_memory_sender_send_is_awaitable() -> None:
    sender = _InMemorySender()
    msg = EmailMessage(to="user@example.com", subject="Hi", html="<p>hi</p>", text="hi")
    await sender.send(msg)
    assert sender.sent == [msg]


# --- EmailConfig defaults -----------------------------------------------------


def test_email_config_defaults_are_well_defined() -> None:
    config = EmailConfig()
    assert config.provider == "smtp"
    assert config.from_email == ""
    assert config.from_name == ""
    assert config.smtp_host == ""
    assert config.smtp_port == 587
    assert config.smtp_use_tls is True
    assert config.smtp_username == ""
    assert config.ses_region == ""
    assert config.ses_access_key_id == ""


def test_email_config_rejects_bad_provider() -> None:
    with pytest.raises(ValidationError):
        EmailConfig(provider="mailgun")  # type: ignore[arg-type]


# --- EmailError ---------------------------------------------------------------


def test_email_error_is_a_ragz_error() -> None:
    assert issubclass(EmailError, RagzError)
    err = EmailError("boom")
    assert isinstance(err, RagzError)
    assert err.detail == "boom"
    assert err.status_code >= 400


# --- settings_service getter/setter round-trip --------------------------------


async def test_email_config_defaults_when_nothing_set(session, settings) -> None:
    out = await settings_service.get_email_config(session)
    assert out == EmailConfig()


async def test_email_config_roundtrips(session, settings) -> None:
    patch = EmailConfig(
        provider="ses",
        from_email="noreply@example.com",
        from_name="Ragz",
        smtp_host="smtp.example.com",
        smtp_port=2525,
        smtp_use_tls=False,
        smtp_username="user@example.com",
        ses_region="us-east-1",
        ses_access_key_id="AKIAEXAMPLE",
    )
    updated = await settings_service.update_email_config(session, patch)
    assert updated == patch

    fetched = await settings_service.get_email_config(session)
    assert fetched == patch


async def test_email_config_update_persists_across_reads(session, settings) -> None:
    await settings_service.update_email_config(
        session, EmailConfig(provider="smtp", smtp_use_tls=False)
    )
    fetched = await settings_service.get_email_config(session)
    assert fetched.provider == "smtp"
    assert fetched.smtp_use_tls is False
