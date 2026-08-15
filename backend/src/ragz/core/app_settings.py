import base64
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ragz.core import crypto
from ragz.core.config import Settings
from ragz.core.db import Base

SIGNING_KEY_NAME = "jwt_signing_key"

# Self-describing prefix marking an AppSetting.value that holds an encrypted
# (envelope AES-256-GCM under the KEK) payload rather than legacy plaintext.
# Format: "enc:v<KEY_VERSION>:<b64(nonce)>:<b64(ciphertext)>".
_ENC_PREFIX = "enc:"


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


def _encode_encrypted(settings: Settings, plaintext: str) -> str:
    """Encrypt `plaintext` under the KEK and render the tagged at-rest string."""
    kek = crypto.load_kek(settings.kek_file)
    nonce, ciphertext = crypto.encrypt(kek, plaintext)
    b64_nonce = base64.b64encode(nonce).decode()
    b64_ct = base64.b64encode(ciphertext).decode()
    return f"{_ENC_PREFIX}v{crypto.KEY_VERSION}:{b64_nonce}:{b64_ct}"


def _decode_encrypted(settings: Settings, stored: str) -> str:
    """Reverse `_encode_encrypted`: decrypt a tagged at-rest string to plaintext."""
    # stored == "enc:v<ver>:<b64nonce>:<b64ct>" -> split off the prefix, then
    # the three colon-delimited fields (version tag / nonce / ciphertext).
    _, ver_tag, b64_nonce, b64_ct = stored.split(":", 3)
    nonce = base64.b64decode(b64_nonce)
    ciphertext = base64.b64decode(b64_ct)
    kek = crypto.load_kek(settings.kek_file)
    return crypto.decrypt(kek, nonce, ciphertext)


async def get_or_create_signing_key(session: AsyncSession, settings: Settings) -> str:
    """Return the PLAINTEXT JWT signing key, encrypting it at rest under the KEK.

    RAGZ-PUB-07: the signing key must never sit as plaintext in the DB (a
    read-only backup would let an attacker forge a superadmin token). The row
    stores a tagged AES-256-GCM ciphertext; callers still receive the raw key
    (token signing/verification needs it byte-identical to what was issued).

    Backward-compat lazy-upgrade: an EXISTING legacy plaintext row (written
    before this fix, no `enc:` tag) is returned as-is AND re-stored encrypted
    in place on first read after deploy. No migration, no forced logout -- the
    returned key is unchanged, so every live token keeps verifying.
    """
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == SIGNING_KEY_NAME))
    ).scalar_one_or_none()

    if row is None:
        # New install: generate, store encrypted, return the plaintext.
        key = secrets.token_urlsafe(32)
        row = AppSetting(key=SIGNING_KEY_NAME, value=_encode_encrypted(settings, key))
        session.add(row)
        await session.commit()
        return key

    if row.value.startswith(_ENC_PREFIX):
        # Already encrypted (the steady state): decrypt and return.
        return _decode_encrypted(settings, row.value)

    # Legacy plaintext key: use it verbatim (tokens stay valid) but upgrade the
    # at-rest representation to encrypted in place so the leak surface closes on
    # first read after deploy -- no token invalidation.
    legacy_plaintext = row.value
    row.value = _encode_encrypted(settings, legacy_plaintext)
    await session.commit()
    return legacy_plaintext


async def get_app_setting(session: AsyncSession, key: str) -> str | None:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    return None if row is None else row.value


async def set_app_setting(
    session: AsyncSession, key: str, value: str, *, commit: bool = True
) -> None:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    if commit:
        await session.commit()
    else:
        await session.flush()
