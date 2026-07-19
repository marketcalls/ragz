"""Secrets service (iron rule 3).

Write path: set_secret / delete_secret. Read path for humans: list_secrets
(name + fingerprint + last_used_at only — never plaintext).

_get_secret_decrypted is the SINGLE decryption path in the entire codebase.
It is deliberately underscore-named: the only sanctioned caller is
raghub.modules.models.sync (LiteLLM config replay). A source-scan test
(tests/modules/models/test_sync.py) enforces this.
"""

from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.config import Settings
from raghub.core.db import naive_utc
from raghub.core.errors import NotFoundError
from raghub.modules.audit.service import record_audit
from raghub.modules.secrets import crypto
from raghub.modules.secrets.models import Secret


async def set_secret(
    session: AsyncSession,
    *,
    actor_id: UUID | None,
    name: str,
    value: str,
    settings: Settings,
    commit: bool = True,
) -> Secret:
    kek = crypto.load_kek(settings.kek_file)
    nonce, ciphertext = crypto.encrypt(kek, value)
    row = (await session.execute(select(Secret).where(Secret.name == name))).scalar_one_or_none()
    if row is None:
        row = Secret(
            name=name,
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=crypto.KEY_VERSION,
            fingerprint=crypto.fingerprint(value),
        )
        session.add(row)
    else:
        row.ciphertext = ciphertext
        row.nonce = nonce
        row.key_version = crypto.KEY_VERSION
        row.fingerprint = crypto.fingerprint(value)
    await record_audit(
        session,
        org_id=None,
        actor_id=actor_id,
        action="secret.written",
        target_type="secret",
        target_id=name,
    )
    if commit:
        await session.commit()
    else:
        await session.flush()
    return row


async def list_secrets(session: AsyncSession) -> list[Secret]:
    return list((await session.execute(select(Secret).order_by(Secret.name))).scalars())


async def delete_secret(
    session: AsyncSession, *, actor_id: UUID | None, name: str, commit: bool = True
) -> None:
    row = (await session.execute(select(Secret).where(Secret.name == name))).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"secret {name!r} not set")
    await session.execute(sa_delete(Secret).where(Secret.name == name))
    await record_audit(
        session,
        org_id=None,
        actor_id=actor_id,
        action="secret.deleted",
        target_type="secret",
        target_id=name,
    )
    if commit:
        await session.commit()
    else:
        await session.flush()


async def _get_secret_decrypted(session: AsyncSession, *, name: str, settings: Settings) -> str:
    """THE single decryption path (iron rule 3). Only modules/models/sync.py calls this."""
    row = (await session.execute(select(Secret).where(Secret.name == name))).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"secret {name!r} not set")
    value = crypto.decrypt(crypto.load_kek(settings.kek_file), row.nonce, row.ciphertext)
    row.last_used_at = naive_utc()
    await session.commit()
    return value
