from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import aiosmtplib

from ragz.modules.email.errors import EmailError
from ragz.modules.email.schemas import EmailMessage


class SmtpSender:
    """`EmailSender` implementation backed by `aiosmtplib` (fully async SMTP,
    no blocking `smtplib`). Credentials are passed in already-decrypted by
    the caller (`modules/email/service.py`); this class never imports or
    touches `modules/secrets`."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool,
        from_email: str,
        from_name: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._from_email = from_email
        self._from_name = from_name

    def _build_mime(self, message: EmailMessage) -> MIMEMultipart:
        mime = MIMEMultipart("alternative")
        mime["From"] = (
            formataddr((self._from_name, self._from_email))
            if self._from_name
            else self._from_email
        )
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.attach(MIMEText(message.text, "plain"))
        mime.attach(MIMEText(message.html, "html"))
        return mime

    async def send(self, message: EmailMessage) -> None:
        mime = self._build_mime(message)
        client = aiosmtplib.SMTP(hostname=self._host, port=self._port)
        try:
            await client.connect()
            if self._use_tls:
                await client.starttls()
            if self._username:
                await client.login(self._username, self._password)
            await client.send_message(mime)
            await client.quit()
        except (aiosmtplib.SMTPException, OSError) as exc:
            raise EmailError(f"SMTP send failed: {exc}") from exc
        finally:
            client.close()
