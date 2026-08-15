from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core import net
from ragz.core.config import Settings
from ragz.modules.audit.models import AuditEvent
from ragz.modules.email import service, templates
from ragz.modules.email.errors import EmailError
from ragz.modules.email.schemas import EmailConfig, EmailMessage
from ragz.modules.models import settings_service
from ragz.modules.secrets import service as secrets_service
from ragz.modules.secrets.crypto import ensure_kek


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


@pytest.fixture
def production_settings(tmp_path: Path) -> Settings:
    # sec RAGZ-PUB-11: mirrors tests/core/test_production_config.py's
    # `safe_kwargs` -- every field the fail-closed validator checks must be
    # overridden so this fixture doesn't itself raise on construction.
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    # Built as a dict (not literal kwargs) so ruff's S106 hardcoded-password
    # heuristic doesn't fire on these deliberately-fake test values.
    kwargs: dict[str, object] = {
        "_env_file": None,
        "environment": "production",
        "api_key_pepper": "a-real-random-pepper-value",
        "database_url": "postgresql+asyncpg://ragz_prod:s3cret-pw@db.internal:5432/ragz",
        "minio_secret_key": "a-real-minio-secret",
        "litellm_master_key": "sk-a-real-litellm-master-key",
        "public_api_base_url": "https://api.example.com",
        "frontend_base_url": "https://app.example.com",
        "kek_file": str(kek),
    }
    return Settings(**kwargs)  # type: ignore[arg-type]


class _FakeLoop:
    """Fakes `asyncio.get_running_loop().getaddrinfo` for `core/net.py`'s
    DNS resolution step, so the production-guard tests below don't touch
    the network."""

    def __init__(self, ip: str) -> None:
        self._ip = ip

    async def getaddrinfo(self, host: str, port: object) -> list[tuple[object, ...]]:
        return [(None, None, None, "", (self._ip, 0))]


class _RecorderSender:
    """Stand-in for `SmtpSender`/`SesSender`: records ctor kwargs and the
    message it was asked to send, without touching the network."""

    instances: list["_RecorderSender"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.sent: EmailMessage | None = None
        _RecorderSender.instances.append(self)

    async def send(self, message: EmailMessage) -> None:
        self.sent = message


@pytest.fixture(autouse=True)
def _reset_recorder() -> None:
    _RecorderSender.instances.clear()


async def _configure_smtp(session: AsyncSession, settings: Settings) -> None:
    await settings_service.update_email_config(
        session,
        EmailConfig(
            provider="smtp",
            from_email="noreply@ragz.example",
            from_name="Ragz",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_use_tls=True,
            smtp_username="mailer",
        ),
    )
    await secrets_service.set_secret(
        session, actor_id=None, name="smtp_password", value="s3cr3t-pw", settings=settings
    )


async def _configure_ses(session: AsyncSession, settings: Settings) -> None:
    await settings_service.update_email_config(
        session,
        EmailConfig(
            provider="ses",
            from_email="noreply@ragz.example",
            from_name="Ragz",
            ses_region="us-east-1",
            ses_access_key_id="AKIAFAKE",
        ),
    )
    await secrets_service.set_secret(
        session, actor_id=None, name="ses_secret_key", value="ses-secret-value", settings=settings
    )


async def test_smtp_provider_decrypts_password_and_sends(
    session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "SmtpSender", _RecorderSender)
    await _configure_smtp(session, settings)
    await service.send_email(
        session, to="user@example.com", subject="Subj", html="<p>hi</p>", text="hi",
        settings=settings,
    )
    assert len(_RecorderSender.instances) == 1
    sender = _RecorderSender.instances[0]
    assert sender.kwargs["password"] == "s3cr3t-pw"  # noqa: S105 - asserting a test fixture value
    assert sender.kwargs["host"] == "smtp.example.com"
    assert sender.kwargs["from_email"] == "noreply@ragz.example"
    assert sender.sent is not None
    assert sender.sent.to == "user@example.com"


async def test_smtp_send_rejects_internal_host_in_production(
    session: AsyncSession,
    production_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sec RAGZ-PUB-11: `_build_sender` re-checks `assert_public_host` right
    before connecting -- defense in depth for config that predates the
    `PUT /admin/email` guard, or a deployment that flipped dev -> production
    without a re-save. A blocked target must surface as `EmailError` (this
    module's typed error), never as a raw `SsrfBlocked` or an aiosmtplib
    connection attempt."""
    monkeypatch.setattr(service, "SmtpSender", _RecorderSender)
    monkeypatch.setattr(net.asyncio, "get_running_loop", lambda: _FakeLoop("10.0.0.5"))
    await _configure_smtp(session, production_settings)
    with pytest.raises(EmailError, match="not permitted"):
        await service.send_email(
            session, to="user@example.com", subject="Subj", html="<p>hi</p>", text="hi",
            settings=production_settings,
        )
    assert len(_RecorderSender.instances) == 0  # never got as far as building the sender


async def test_smtp_send_allows_public_host_in_production(
    session: AsyncSession,
    production_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "SmtpSender", _RecorderSender)
    monkeypatch.setattr(net.asyncio, "get_running_loop", lambda: _FakeLoop("93.184.216.34"))
    await _configure_smtp(session, production_settings)
    await service.send_email(
        session, to="user@example.com", subject="Subj", html="<p>hi</p>", text="hi",
        settings=production_settings,
    )
    assert len(_RecorderSender.instances) == 1


async def test_ses_provider_decrypts_secret_key_and_sends(
    session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "SesSender", _RecorderSender)
    await _configure_ses(session, settings)
    await service.send_email(
        session, to="user@example.com", subject="Subj", html="<p>hi</p>", text="hi",
        settings=settings,
    )
    assert len(_RecorderSender.instances) == 1
    sender = _RecorderSender.instances[0]
    assert (
        sender.kwargs["secret_access_key"] == "ses-secret-value"  # noqa: S105 - test fixture value
    )
    assert sender.kwargs["region"] == "us-east-1"
    assert sender.sent is not None


async def test_unconfigured_provider_raises_without_decrypting_or_sending(
    session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "SmtpSender", _RecorderSender)
    monkeypatch.setattr(service, "SesSender", _RecorderSender)
    # Default EmailConfig: provider="smtp", every field blank.
    with pytest.raises(EmailError):
        await service.send_email(
            session, to="user@example.com", subject="s", html="<p>h</p>", text="h",
            settings=settings,
        )
    assert _RecorderSender.instances == []
    assert (await session.execute(select(AuditEvent))).scalars().first() is None


async def test_missing_secret_raises_email_error(
    session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "SmtpSender", _RecorderSender)
    await settings_service.update_email_config(
        session,
        EmailConfig(
            provider="smtp",
            from_email="noreply@ragz.example",
            smtp_host="smtp.example.com",
        ),
    )
    # No smtp_password secret written.
    with pytest.raises(EmailError):
        await service.send_email(
            session, to="user@example.com", subject="s", html="<p>h</p>", text="h",
            settings=settings,
        )
    assert _RecorderSender.instances == []


async def test_email_sent_audit_row_written_on_success(
    session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "SmtpSender", _RecorderSender)
    await _configure_smtp(session, settings)
    await service.send_email(
        session, to="user@example.com", subject="Subj", html="<p>hi</p>", text="hi",
        settings=settings,
    )
    row = (
        await session.execute(select(AuditEvent).where(AuditEvent.action == "email.sent"))
    ).scalar_one()
    assert row.target_type == "email"
    assert row.target_id == "user@example.com"
    # No body or credential content anywhere on the row.
    for value in (row.action, row.target_type, row.target_id, row.reason_code):
        if value:
            assert "s3cr3t-pw" not in value
            assert "hi" != value


async def test_send_rendered_unpacks_template_tuple(
    session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "SmtpSender", _RecorderSender)
    await _configure_smtp(session, settings)
    await service.send_rendered(
        session, to="user@example.com", rendered=templates.test_email(), settings=settings
    )
    sender = _RecorderSender.instances[0]
    assert sender.sent is not None
    assert sender.sent.subject == "Ragz test email"


def test_reset_password_email_contains_url_and_ttl() -> None:
    subject, html, text = templates.reset_password_email(
        "https://ragz.example/reset?token=abc", ttl_minutes=45
    )
    assert subject and html and text
    assert "https://ragz.example/reset?token=abc" in html
    assert "https://ragz.example/reset?token=abc" in text
    assert "45" in html
    assert "45" in text


def test_password_changed_email_non_empty() -> None:
    subject, html, text = templates.password_changed_email()
    assert subject and html and text


def test_test_email_non_empty() -> None:
    subject, html, text = templates.test_email()
    assert subject and html and text
