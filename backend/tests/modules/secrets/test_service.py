from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.core.errors import NotFoundError, SecretsError
from raghub.modules.audit.models import AuditEvent
from raghub.modules.secrets.crypto import ensure_kek
from raghub.modules.secrets.models import Secret
from raghub.modules.secrets.service import (
    _get_secret_decrypted,
    delete_secret,
    list_secrets,
    set_secret,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


async def test_set_get_roundtrip_updates_last_used(
    session: AsyncSession, settings: Settings
) -> None:
    await set_secret(
        session, actor_id=None, name="model:x", value="sk-live-1234", settings=settings
    )
    row = (await session.execute(select(Secret).where(Secret.name == "model:x"))).scalar_one()
    assert row.last_used_at is None
    assert b"sk-live-1234" not in row.ciphertext
    assert await _get_secret_decrypted(session, name="model:x", settings=settings) == "sk-live-1234"
    await session.refresh(row)
    assert row.last_used_at is not None


async def test_set_secret_upserts_and_audits(session: AsyncSession, settings: Settings) -> None:
    await set_secret(session, actor_id=None, name="k", value="one", settings=settings)
    await set_secret(session, actor_id=None, name="k", value="two", settings=settings)
    assert len(await list_secrets(session)) == 1
    assert await _get_secret_decrypted(session, name="k", settings=settings) == "two"
    actions = [
        e.action for e in (await session.execute(select(AuditEvent))).scalars()
    ]
    assert actions == ["secret.written", "secret.written"]


async def test_unknown_secret_raises(session: AsyncSession, settings: Settings) -> None:
    with pytest.raises(NotFoundError):
        await _get_secret_decrypted(session, name="ghost", settings=settings)


async def test_wrong_kek_fails_closed(
    session: AsyncSession, settings: Settings, tmp_path: Path
) -> None:
    await set_secret(session, actor_id=None, name="k", value="v", settings=settings)
    other = tmp_path / "other-kek"
    ensure_kek(str(other))
    bad = Settings(_env_file=None, kek_file=str(other))
    with pytest.raises(SecretsError):
        await _get_secret_decrypted(session, name="k", settings=bad)


async def test_delete_secret_removes_and_audits(session: AsyncSession, settings: Settings) -> None:
    await set_secret(session, actor_id=None, name="k", value="v", settings=settings)
    await delete_secret(session, actor_id=None, name="k")
    assert await list_secrets(session) == []
    actions = [
        e.action for e in (await session.execute(select(AuditEvent))).scalars()
    ]
    assert actions == ["secret.written", "secret.deleted"]


async def test_delete_secret_missing_raises(session: AsyncSession, settings: Settings) -> None:
    with pytest.raises(NotFoundError):
        await delete_secret(session, actor_id=None, name="ghost")
