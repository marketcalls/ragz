from typing import Protocol

from ragz.modules.email.schemas import EmailMessage


class EmailSender(Protocol):
    """Async transport for a single email. Implementations (`SmtpSender`,
    `SesSender` -- later tasks) receive already-decrypted credentials; they
    never touch `modules/secrets` themselves."""

    async def send(self, message: EmailMessage) -> None: ...
