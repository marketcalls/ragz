"""Provider-selecting email service.

Iron rule 3 note: newest sanctioned caller of `secrets._get_secret_decrypted`
(alongside `models/sync.py`, `auth/oidc.py`, `models/keys.py`, `chat/web.py`,
`retrieval/rerank.py`, `documents/parsers.py`, `bots/service.py`) --
`smtp_password`/`ses_secret_key`, decrypted in memory for exactly one
outbound send and never returned, logged, or persisted. Named in the
allowlist test (`tests/modules/models/test_sync.py`). Decryption stays
STRICTLY inside this module -- `SmtpSender`/`SesSender` receive plaintext
credentials already resolved here and never import `modules/secrets`
themselves.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import Settings
from ragz.core.errors import NotFoundError, SsrfBlocked
from ragz.core.net import assert_public_host
from ragz.modules.audit.service import record_audit
from ragz.modules.email.errors import EmailError
from ragz.modules.email.schemas import EmailConfig, EmailMessage
from ragz.modules.email.ses_sender import SesSender
from ragz.modules.email.smtp_sender import SmtpSender
from ragz.modules.models import settings_service
from ragz.modules.secrets import service as secrets_service

_SMTP_SECRET = "smtp_password"  # noqa: S105 - a secret NAME, not a secret
_SES_SECRET = "ses_secret_key"  # noqa: S105 - a secret NAME, not a secret


def _require_configured(config: EmailConfig) -> None:
    """Raise `EmailError` for an unconfigured provider before any secret is
    touched or any send is attempted -- never send half-configured."""
    if not config.from_email:
        raise EmailError("email is not configured: from_email is blank")
    if config.provider == "smtp" and not config.smtp_host:
        raise EmailError("SMTP email is not configured: smtp_host is blank")
    if config.provider == "ses" and not config.ses_region:
        raise EmailError("SES email is not configured: ses_region is blank")


async def _build_sender(
    session: AsyncSession, config: EmailConfig, *, settings: Settings
) -> SmtpSender | SesSender:
    if config.provider == "smtp":
        # sec RAGZ-PUB-11: guard again at the actual connect path, not just
        # at `PUT /admin/email` -- the stored config could predate the
        # guard (or the deployment could have flipped dev -> production
        # without a re-save). No-op in dev/test; production/staging only.
        try:
            await assert_public_host(config.smtp_host, settings)
        except SsrfBlocked as exc:
            raise EmailError(f"SMTP host is not permitted: {exc.detail}") from exc
        try:
            password = await secrets_service._get_secret_decrypted(  # noqa: SLF001
                session, name=_SMTP_SECRET, settings=settings
            )
        except NotFoundError as exc:
            raise EmailError("SMTP password is not configured") from exc
        return SmtpSender(
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.smtp_username,
            password=password,
            use_tls=config.smtp_use_tls,
            from_email=config.from_email,
            from_name=config.from_name,
        )
    try:
        secret_key = await secrets_service._get_secret_decrypted(  # noqa: SLF001
            session, name=_SES_SECRET, settings=settings
        )
    except NotFoundError as exc:
        raise EmailError("SES secret key is not configured") from exc
    return SesSender(
        region=config.ses_region,
        access_key_id=config.ses_access_key_id,
        secret_access_key=secret_key,
        from_email=config.from_email,
        from_name=config.from_name,
    )


async def send_email(
    session: AsyncSession,
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
    settings: Settings,
) -> None:
    """Load the active `EmailConfig`, decrypt its provider secret, send, and
    audit the send (recipient + action only -- never body or credential).
    Raises `EmailError` for an unconfigured provider or a missing secret;
    a `EmailError` raised by the sender itself propagates unchanged."""
    config = await settings_service.get_email_config(session)
    _require_configured(config)
    sender = await _build_sender(session, config, settings=settings)
    await sender.send(EmailMessage(to=to, subject=subject, html=html, text=text))
    await record_audit(
        session,
        org_id=None,
        actor_id=None,
        action="email.sent",
        target_type="email",
        target_id=to,
    )
    await session.commit()


async def send_rendered(
    session: AsyncSession,
    *,
    to: str,
    rendered: tuple[str, str, str],
    settings: Settings,
) -> None:
    """Convenience wrapper for callers holding a `templates.py` tuple
    (subject, html, text) -- keeps call sites reading naturally, e.g.
    `send_rendered(session, to=user.email, rendered=reset_password_email(url,
    ttl_minutes=45), settings=settings)`."""
    subject, html, text = rendered
    await send_email(session, to=to, subject=subject, html=html, text=text, settings=settings)
