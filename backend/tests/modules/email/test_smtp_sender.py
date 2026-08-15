from typing import Any

import aiosmtplib
import pytest

from ragz.modules.email.errors import EmailError
from ragz.modules.email.schemas import EmailMessage
from ragz.modules.email.smtp_sender import SmtpSender


class _FakeSMTP:
    """Records every call a real `aiosmtplib.SMTP` would receive, without
    touching the network."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, *, hostname: str, port: int) -> None:
        self.hostname = hostname
        self.port = port
        self.connected = False
        self.starttls_called = False
        self.login_args: tuple[Any, Any] | None = None
        self.sent_message: Any = None
        self.quit_called = False
        self.close_called = False
        _FakeSMTP.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def starttls(self) -> None:
        self.starttls_called = True

    async def login(self, username: Any, password: Any) -> None:
        self.login_args = (username, password)

    async def send_message(self, message: Any) -> None:
        self.sent_message = message

    async def quit(self) -> None:
        self.quit_called = True

    def close(self) -> None:
        self.close_called = True


class _RaisingSMTP(_FakeSMTP):
    """Fake whose `send_message` raises, to exercise the EmailError path."""

    def __init__(self, *, hostname: str, port: int, exc: BaseException) -> None:
        super().__init__(hostname=hostname, port=port)
        self._exc = exc

    async def send_message(self, message: Any) -> None:
        raise self._exc


@pytest.fixture(autouse=True)
def _reset_instances() -> None:
    _FakeSMTP.instances.clear()


@pytest.fixture
def message() -> EmailMessage:
    return EmailMessage(
        to="user@example.com",
        subject="Hello",
        html="<p>hi</p>",
        text="hi",
    )


def _sender(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> SmtpSender:
    kwargs: dict[str, Any] = dict(
        host="smtp.example.com",
        port=587,
        username="",
        password="",
        use_tls=True,
        from_email="noreply@example.com",
        from_name="",
    )
    kwargs.update(overrides)
    monkeypatch.setattr(aiosmtplib, "SMTP", _FakeSMTP)
    return SmtpSender(**kwargs)


async def test_send_connects_with_correct_hostname_and_port(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    sender = _sender(monkeypatch, host="smtp.example.com", port=2525)
    await sender.send(message)

    assert len(_FakeSMTP.instances) == 1
    fake = _FakeSMTP.instances[0]
    assert fake.hostname == "smtp.example.com"
    assert fake.port == 2525
    assert fake.connected is True


async def test_send_calls_starttls_when_use_tls_true(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    sender = _sender(monkeypatch, use_tls=True)
    await sender.send(message)

    assert _FakeSMTP.instances[0].starttls_called is True


async def test_send_skips_starttls_when_use_tls_false(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    sender = _sender(monkeypatch, use_tls=False)
    await sender.send(message)

    assert _FakeSMTP.instances[0].starttls_called is False


async def test_send_calls_login_when_username_set(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    sender = _sender(monkeypatch, username="user@example.com", password="s3cret")  # noqa: S106
    await sender.send(message)

    assert _FakeSMTP.instances[0].login_args == ("user@example.com", "s3cret")


async def test_send_skips_login_when_username_not_set(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    sender = _sender(monkeypatch, username="", password="")
    await sender.send(message)

    assert _FakeSMTP.instances[0].login_args is None


async def test_send_builds_mime_message_with_headers_and_both_parts(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    sender = _sender(
        monkeypatch,
        from_email="noreply@example.com",
        from_name="Ragz",
    )
    await sender.send(message)

    sent = _FakeSMTP.instances[0].sent_message
    assert sent is not None
    assert sent["From"] == "Ragz <noreply@example.com>"
    assert sent["To"] == "user@example.com"
    assert sent["Subject"] == "Hello"

    parts = sent.get_payload()
    assert len(parts) == 2
    content_types = {part.get_content_type() for part in parts}
    assert content_types == {"text/plain", "text/html"}
    for part in parts:
        payload = part.get_payload(decode=True).decode("utf-8")
        if part.get_content_type() == "text/plain":
            assert payload == "hi"
        else:
            assert payload == "<p>hi</p>"


async def test_send_from_header_omits_angle_brackets_when_no_from_name(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    sender = _sender(monkeypatch, from_email="noreply@example.com", from_name="")
    await sender.send(message)

    assert _FakeSMTP.instances[0].sent_message["From"] == "noreply@example.com"


async def test_send_quits_and_closes_on_success(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    sender = _sender(monkeypatch)
    await sender.send(message)

    fake = _FakeSMTP.instances[0]
    assert fake.quit_called is True
    assert fake.close_called is True


async def test_send_wraps_aiosmtplib_error_in_email_error(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    def factory(*, hostname: str, port: int) -> _RaisingSMTP:
        return _RaisingSMTP(
            hostname=hostname,
            port=port,
            exc=aiosmtplib.SMTPAuthenticationError(535, "bad creds"),
        )

    monkeypatch.setattr(aiosmtplib, "SMTP", factory)
    sender = SmtpSender(
        host="smtp.example.com",
        port=587,
        username="user@example.com",
        password="wrong",  # noqa: S106
        use_tls=True,
        from_email="noreply@example.com",
        from_name="",
    )

    with pytest.raises(EmailError):
        await sender.send(message)

    assert _FakeSMTP.instances[0].close_called is True


async def test_send_wraps_connection_error_in_email_error(
    monkeypatch: pytest.MonkeyPatch, message: EmailMessage
) -> None:
    def factory(*, hostname: str, port: int) -> _RaisingSMTP:
        return _RaisingSMTP(
            hostname=hostname,
            port=port,
            exc=ConnectionRefusedError("connection refused"),
        )

    monkeypatch.setattr(aiosmtplib, "SMTP", factory)
    sender = SmtpSender(
        host="smtp.example.com",
        port=587,
        username="",
        password="",
        use_tls=True,
        from_email="noreply@example.com",
        from_name="",
    )

    with pytest.raises(EmailError):
        await sender.send(message)
