import hashlib
import hmac
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ragz.modules.bots import verify


def test_verify_telegram_valid_and_tampered() -> None:
    secret = "tg-webhook-secret"  # noqa: S105 - test fixture, not a real secret
    assert verify.verify_telegram({"x-telegram-bot-api-secret-token": secret}, b"{}", secret)
    assert not verify.verify_telegram({"x-telegram-bot-api-secret-token": "wrong"}, b"{}", secret)
    assert not verify.verify_telegram({}, b"{}", secret)


def _slack_headers(secret: str, body: bytes, timestamp: int) -> dict[str, str]:
    basestring = f"v0:{timestamp}:{body.decode()}".encode()
    sig = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {"x-slack-request-timestamp": str(timestamp), "x-slack-signature": sig}


def test_verify_slack_valid_tampered_body_wrong_secret_and_stale() -> None:
    secret = "slack-signing-secret"  # noqa: S105 - test fixture, not a real secret
    body = b'{"type":"event_callback"}'
    now = int(time.time())
    headers = _slack_headers(secret, body, now)
    assert verify.verify_slack(headers, body, secret, now=now)
    assert not verify.verify_slack(headers, b'{"type":"tampered"}', secret, now=now)
    assert not verify.verify_slack(headers, body, "wrong-secret", now=now)
    stale_headers = _slack_headers(secret, body, now - 400)  # > 5 min old
    assert not verify.verify_slack(stale_headers, body, secret, now=now)
    assert not verify.verify_slack({}, body, secret, now=now)


def test_verify_discord_valid_tampered_and_bad_signature() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key_hex = private_key.public_key().public_bytes_raw().hex()
    now = int(time.time())
    timestamp = str(now)
    body = b'{"type":1}'
    signature_hex = private_key.sign(timestamp.encode() + body).hex()
    headers = {"x-signature-ed25519": signature_hex, "x-signature-timestamp": timestamp}
    assert verify.verify_discord(headers, body, public_key_hex, now=now)
    assert not verify.verify_discord(headers, b'{"type":2}', public_key_hex, now=now)  # tampered
    other_key_hex = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
    assert not verify.verify_discord(headers, body, other_key_hex, now=now)  # wrong public key
    missing_sig_headers = {"x-signature-timestamp": timestamp}
    assert not verify.verify_discord(missing_sig_headers, body, public_key_hex, now=now)  # no sig
    bad_hex_headers = {"x-signature-ed25519": "not-hex!!", "x-signature-timestamp": timestamp}
    assert not verify.verify_discord(bad_hex_headers, body, public_key_hex, now=now)  # not hex


def test_verify_discord_stale_timestamp_rejected() -> None:
    """RAGZ-PUB-10: X-Signature-Timestamp is signed (part of the Ed25519
    message), so unlike Telegram it's a forgery-resistant value worth
    freshness-checking -- mirrors verify_slack's stale-timestamp guard."""
    private_key = Ed25519PrivateKey.generate()
    public_key_hex = private_key.public_key().public_bytes_raw().hex()
    now = int(time.time())
    stale_timestamp = str(now - 400)  # > 5 min old
    body = b'{"type":1}'
    signature_hex = private_key.sign(stale_timestamp.encode() + body).hex()
    headers = {"x-signature-ed25519": signature_hex, "x-signature-timestamp": stale_timestamp}
    assert not verify.verify_discord(headers, body, public_key_hex, now=now)
