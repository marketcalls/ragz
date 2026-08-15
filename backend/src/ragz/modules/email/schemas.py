from typing import Literal

from pydantic import BaseModel, Field

EmailProvider = Literal["smtp", "ses"]


class EmailMessage(BaseModel):
    """A single outbound email. Value object passed to an `EmailSender`."""

    to: str = Field(min_length=1, max_length=320)
    subject: str = Field(min_length=1, max_length=500)
    html: str = Field(min_length=1, max_length=100_000)
    text: str = Field(min_length=1, max_length=100_000)


class EmailConfig(BaseModel):
    """Non-secret email provider configuration (superadmin-managed).

    Secret fields (`smtp_password`, `ses_secret_key`) are deliberately NOT
    here -- they are written/read through `modules/secrets` (KEK-encrypted),
    never through this schema. An unconfigured install gets this schema's
    defaults: provider "smtp" with every field blank/zeroed, which the
    email service (a later task) treats as "not configured" -> `EmailError`.
    """

    provider: EmailProvider = "smtp"
    from_email: str = Field(default="", max_length=320)
    from_name: str = Field(default="", max_length=200)
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=587, ge=0, le=65535)
    smtp_use_tls: bool = True
    smtp_username: str = Field(default="", max_length=320)
    ses_region: str = Field(default="", max_length=64)
    ses_access_key_id: str = Field(default="", max_length=128)


class EmailConfigOut(EmailConfig):
    """`GET`/`PUT /admin/email` response: every non-secret `EmailConfig`
    field plus existence-only booleans for the two provider secrets. The
    secret VALUES are never returned (iron rule 3) -- only whether one has
    been set."""

    smtp_password_set: bool
    ses_secret_key_set: bool


class EmailConfigUpdate(EmailConfig):
    """`PUT /admin/email` body: a full replace of the non-secret config
    (mirrors `settings_service.update_email_config`'s full-`EmailConfig`
    signature) plus two optional write-only secret fields. `None` leaves the
    corresponding stored secret untouched; a provided value is forwarded to
    `modules.secrets.set_secret`. Never echoed back -- `EmailConfigOut` has
    no secret-value fields, only the `*_set` booleans."""

    smtp_password: str | None = Field(default=None, max_length=1024)
    ses_secret_key: str | None = Field(default=None, max_length=1024)


class EmailTestRequest(BaseModel):
    """`POST /admin/email/test` body: recipient for a one-off test send."""

    to: str = Field(min_length=1, max_length=320)
