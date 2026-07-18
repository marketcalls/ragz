"""Idempotent first-run bootstrap: creates the platform org and superadmin.

Usage: RAGHUB_BOOTSTRAP_EMAIL=... RAGHUB_BOOTSTRAP_PASSWORD=... uv run python -m raghub.bootstrap
"""

import asyncio
import os
import sys

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from raghub.core.config import get_settings
from raghub.core.db import build_engine, build_session_factory
from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.tenancy.models import Organization


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
    email = os.environ.get("RAGHUB_BOOTSTRAP_EMAIL")
    password = os.environ.get("RAGHUB_BOOTSTRAP_PASSWORD")
    if not email or not password:
        print("Set RAGHUB_BOOTSTRAP_EMAIL and RAGHUB_BOOTSTRAP_PASSWORD", file=sys.stderr)
        raise SystemExit(2)
    try:
        TypeAdapter(EmailStr).validate_python(email)
    except Exception as exc:
        print(f"Invalid bootstrap email: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if len(password) < 12:
        print("RAGHUB_BOOTSTRAP_PASSWORD must be at least 12 characters", file=sys.stderr)
        raise SystemExit(2)
    factory = build_session_factory(build_engine(get_settings().database_url))
    created = asyncio.run(bootstrap_superadmin(factory, email=email, password=password))
    print("superadmin created" if created else "superadmin already exists")


if __name__ == "__main__":
    main()
