from typing import Any

import pytest
from botocore.exceptions import ClientError

from ragz.modules.email.errors import EmailError
from ragz.modules.email.schemas import EmailMessage
from ragz.modules.email.ses_sender import SesSender


class _FakeSesClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.send_email_kwargs: dict[str, Any] | None = None

    async def __aenter__(self) -> "_FakeSesClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def send_email(self, **kwargs: Any) -> dict[str, Any]:
        self.send_email_kwargs = kwargs
        if self.fail:
            raise ClientError(
                {"Error": {"Code": "MessageRejected", "Message": "boom"}}, "SendEmail"
            )
        return {"MessageId": "fake-message-id"}


class _FakeSession:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.client_calls: list[dict[str, Any]] = []
        self.client_service_names: list[str] = []
        self.last_client: _FakeSesClient | None = None

    def client(self, service_name: str, **kwargs: Any) -> _FakeSesClient:
        self.client_service_names.append(service_name)
        self.client_calls.append(kwargs)
        fake_client = _FakeSesClient(fail=self.fail)
        self.last_client = fake_client
        return fake_client


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()

    def _session_factory(*args: object, **kwargs: object) -> _FakeSession:
        return session

    monkeypatch.setattr("ragz.modules.email.ses_sender.aioboto3.Session", _session_factory)
    return session


@pytest.fixture
def message() -> EmailMessage:
    return EmailMessage(
        to="user@example.com",
        subject="Reset your password",
        html="<p>click <a href='https://x'>here</a></p>",
        text="click here: https://x",
    )


async def test_client_built_with_region_and_credentials(
    fake_session: _FakeSession, message: EmailMessage
) -> None:
    sender = SesSender(
        region="us-east-1",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="shh",  # noqa: S106
        from_email="noreply@example.com",
        from_name="Ragz",
    )
    await sender.send(message)

    assert fake_session.client_service_names == ["ses"]
    assert fake_session.client_calls == [
        {
            "region_name": "us-east-1",
            "aws_access_key_id": "AKIAEXAMPLE",
            "aws_secret_access_key": "shh",
        }
    ]


async def test_send_email_called_with_correct_shape_and_from_name(
    fake_session: _FakeSession, message: EmailMessage
) -> None:
    sender = SesSender(
        region="us-east-1",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="shh",  # noqa: S106
        from_email="noreply@example.com",
        from_name="Ragz",
    )
    await sender.send(message)

    assert fake_session.last_client is not None
    kwargs = fake_session.last_client.send_email_kwargs
    assert kwargs == {
        "Source": "Ragz <noreply@example.com>",
        "Destination": {"ToAddresses": ["user@example.com"]},
        "Message": {
            "Subject": {"Data": "Reset your password"},
            "Body": {
                "Html": {"Data": message.html},
                "Text": {"Data": message.text},
            },
        },
    }


async def test_send_email_source_omits_name_when_from_name_blank(
    fake_session: _FakeSession, message: EmailMessage
) -> None:
    sender = SesSender(
        region="us-east-1",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="shh",  # noqa: S106
        from_email="noreply@example.com",
        from_name="",
    )
    await sender.send(message)

    assert fake_session.last_client is not None
    assert fake_session.last_client.send_email_kwargs["Source"] == "noreply@example.com"


async def test_client_error_surfaces_as_email_error(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    failing_session = _FakeSession(fail=True)
    monkeypatch.setattr(
        "ragz.modules.email.ses_sender.aioboto3.Session",
        lambda *a, **k: failing_session,
    )
    sender = SesSender(
        region="us-east-1",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="shh",  # noqa: S106
        from_email="noreply@example.com",
        from_name="Ragz",
    )

    with pytest.raises(EmailError):
        await sender.send(message)
