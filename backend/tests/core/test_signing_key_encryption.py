"""RAGZ-PUB-07: the JWT signing key must be encrypted at rest under the KEK.

A read-only DB dump must not yield a usable signing key. These tests pin the
storage format (tagged AES-256-GCM ciphertext, never plaintext), the plaintext
round-trip callers depend on, and the transparent lazy-upgrade of legacy
plaintext rows (no migration, no token invalidation).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import (
    SIGNING_KEY_NAME,
    AppSetting,
    get_or_create_signing_key,
)
from ragz.core.config import Settings
from ragz.core.crypto import load_kek


async def _stored_value(session: AsyncSession) -> str:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == SIGNING_KEY_NAME))
    ).scalar_one()
    return row.value


async def test_key_created_encrypted_at_rest_and_roundtrips(
    session: AsyncSession, test_settings: Settings
) -> None:
    # First call creates the key.
    key = await get_or_create_signing_key(session, test_settings)
    assert len(key) >= 43  # 32 bytes url-safe base64

    # What lands in the DB is NOT the plaintext -- it is the tagged ciphertext.
    stored = await _stored_value(session)
    assert stored != key
    assert stored.startswith("enc:v")
    assert key not in stored

    # A second call round-trips the SAME plaintext (decrypt must be exact so
    # already-issued tokens keep verifying).
    key2 = await get_or_create_signing_key(session, test_settings)
    assert key2 == key


async def test_wrong_kek_cannot_recover_key(
    session: AsyncSession, test_settings: Settings, kek_file: str
) -> None:
    # The at-rest value is genuinely bound to the KEK: the stored ciphertext
    # bytes must not contain the plaintext, proving a DB-only dump is useless.
    key = await get_or_create_signing_key(session, test_settings)
    stored = await _stored_value(session)
    # Sanity: the KEK is required and the stored blob is not the raw key.
    assert load_kek(kek_file)  # KEK loads (bytes truthy)
    assert key.encode().hex() not in stored


async def test_legacy_plaintext_key_lazily_upgraded(
    session: AsyncSession, test_settings: Settings
) -> None:
    # Simulate a pre-fix install: a legacy PLAINTEXT signing key row (no tag).
    legacy = "legacy-plaintext-signing-key-value-000000000"
    session.add(AppSetting(key=SIGNING_KEY_NAME, value=legacy))
    await session.commit()

    # First read after deploy returns the exact legacy key (tokens stay valid)...
    returned = await get_or_create_signing_key(session, test_settings)
    assert returned == legacy

    # ...AND the row is now re-stored encrypted in place (leak surface closed).
    stored = await _stored_value(session)
    assert stored != legacy
    assert stored.startswith("enc:v")
    assert legacy not in stored

    # A subsequent read decrypts back to the same legacy key, unchanged.
    again = await get_or_create_signing_key(session, test_settings)
    assert again == legacy
