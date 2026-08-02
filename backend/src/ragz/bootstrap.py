"""Idempotent first-run bootstrap: creates the platform org and superadmin.

Usage: RAGZ_BOOTSTRAP_EMAIL=... RAGZ_BOOTSTRAP_PASSWORD=... uv run python -m ragz.bootstrap
"""

import asyncio
import os
import sys

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragz.core.config import get_settings
from ragz.core.db import build_engine, build_session_factory
from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.secrets.crypto import ensure_kek
from ragz.modules.tenancy.models import Organization


async def bootstrap_superadmin(
    session_factory: async_sessionmaker[AsyncSession], *, email: str, password: str
) -> bool:
    async with session_factory() as session:
        existing = (
            await session.execute(select(User).where(User.role == "superadmin"))
        ).scalar_one_or_none()
        if existing is not None:
            return False
        org = Organization(name="Platform")
        session.add(org)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return False
        session.add(
            User(
                org_id=org.id,
                email=email,
                password_hash=hash_password(password),
                role="superadmin",
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False
        return True


def main() -> None:
    email = os.environ.get("RAGZ_BOOTSTRAP_EMAIL")
    password = os.environ.get("RAGZ_BOOTSTRAP_PASSWORD")
    if not email or not password:
        print("Set RAGZ_BOOTSTRAP_EMAIL and RAGZ_BOOTSTRAP_PASSWORD", file=sys.stderr)
        raise SystemExit(2)
    try:
        TypeAdapter(EmailStr).validate_python(email)
    except Exception as exc:
        print(f"Invalid bootstrap email: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if len(password) < 12:
        print("RAGZ_BOOTSTRAP_PASSWORD must be at least 12 characters", file=sys.stderr)
        raise SystemExit(2)
    settings = get_settings()
    ensure_kek(settings.kek_file)
    print(f"KEK ready at {settings.kek_file}")
    factory = build_session_factory(build_engine(settings.database_url))
    created = asyncio.run(bootstrap_superadmin(factory, email=email, password=password))
    print("superadmin created" if created else "superadmin already exists")


if __name__ == "__main__":
    main()
