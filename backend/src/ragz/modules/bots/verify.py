"""The three chat-platform signature verifiers (design decisions §Decisions):
pure functions over (headers, raw_body, secret) -> bool, no HTTP involved --
unit-testable by crafting real signatures with the same algorithms the
platforms use. Callers (api/routes/bots.py) MUST reject on False -> 401
BEFORE any workspace/LLM work (iron rule: signature verification mandatory)."""

import hashlib
import hmac
import time
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_SLACK_MAX_SKEW_SECONDS = 300  # 5-minute replay guard (design decision)
_DISCORD_MAX_SKEW_SECONDS = 300  # RAGZ-PUB-10: mirrors Slack's freshness bound


def verify_telegram(headers: Mapping[str, str], raw_body: bytes, secret: str) -> bool:
    """Telegram sets X-Telegram-Bot-Api-Secret-Token to the value Ragz chose
    when registering the webhook; constant-time compare to the stored copy.

    RAGZ-PUB-10: unlike Slack/Discord, this is a static shared secret, not an
    HMAC/signature computed over the body plus a timestamp -- Telegram sends
    no header that is cryptographically bound to a delivery time, so there is
    no forgery-resistant value here to freshness-check (an attacker holding
    the leaked secret could set any body field, including a message `date`,
    to whatever they like). Replay protection for Telegram therefore comes
    entirely from api/routes/bots.py's idempotency claim on `update_id`, not
    from a timestamp check in this function."""
    header_value = headers.get("x-telegram-bot-api-secret-token", "")
    return bool(header_value) and hmac.compare_digest(header_value, secret)


def verify_slack(
    headers: Mapping[str, str], raw_body: bytes, secret: str, *, now: float | None = None
) -> bool:
    """HMAC-SHA256 of 'v0:{timestamp}:{raw body}' with the signing secret,
    constant-time compared to X-Slack-Signature; rejects a timestamp more
    than 5 minutes from `now` (replay guard). `now` is injectable for tests."""
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    if not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    current = now if now is not None else time.time()
    if abs(current - ts) > _SLACK_MAX_SKEW_SECONDS:
        return False
    basestring = f"v0:{timestamp}:".encode() + raw_body
    computed = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


def verify_discord(
    headers: Mapping[str, str], raw_body: bytes, public_key_hex: str, *, now: float | None = None
) -> bool:
    """Ed25519 verify of '{timestamp}{raw body}' against the app's public key
    (hex-encoded, stored as the integration's signing secret) using the
    X-Signature-Ed25519 header. cryptography is already a dependency
    (envelope AES-GCM) -- no new dep.

    RAGZ-PUB-10: X-Signature-Timestamp is itself part of the signed message
    (it's concatenated with the body before Ed25519 verification below), so
    -- unlike Telegram's unsigned static secret -- it's a value an attacker
    cannot forge without already holding a valid signature over it. Rejecting
    a stale one closes the replay window the same way Slack's timestamp
    check does; `now` is injectable for tests."""
    signature_hex = headers.get("x-signature-ed25519", "")
    timestamp = headers.get("x-signature-timestamp", "")
    if not signature_hex or not timestamp:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    current = now if now is not None else time.time()
    if abs(current - ts) > _DISCORD_MAX_SKEW_SECONDS:
        return False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature_hex), timestamp.encode() + raw_body)
        return True
    except (InvalidSignature, ValueError):
        return False
