from typing import Any

import aioboto3
from botocore.exceptions import BotoCoreError, ClientError

from ragz.modules.email.errors import EmailError
from ragz.modules.email.schemas import EmailMessage


class SesSender:
    """`EmailSender` implementation backed by Amazon SES (aioboto3).

    Credentials are passed in already-decrypted; this class never imports or
    touches `modules/secrets`.
    """

    def __init__(
        self,
        *,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        from_email: str,
        from_name: str,
    ) -> None:
        self._session = aioboto3.Session()
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._from_email = from_email
        self._from_name = from_name

    def _client(self) -> Any:
        return self._session.client(
            "ses",
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
        )

    @property
    def _source(self) -> str:
        if self._from_name:
            return f"{self._from_name} <{self._from_email}>"
        return self._from_email

    async def send(self, message: EmailMessage) -> None:
        try:
            async with self._client() as ses:
                await ses.send_email(
                    Source=self._source,
                    Destination={"ToAddresses": [message.to]},
                    Message={
                        "Subject": {"Data": message.subject},
                        "Body": {
                            "Html": {"Data": message.html},
                            "Text": {"Data": message.text},
                        },
                    },
                )
        except (ClientError, BotoCoreError) as exc:
            raise EmailError(f"SES send failed: {exc}") from exc
