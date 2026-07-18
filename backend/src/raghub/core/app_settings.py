import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from raghub.core.db import Base

SIGNING_KEY_NAME = "jwt_signing_key"


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


async def get_or_create_signing_key(session: AsyncSession) -> str:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == SIGNING_KEY_NAME))
    ).scalar_one_or_none()
    if row is None:
        row = AppSetting(key=SIGNING_KEY_NAME, value=secrets.token_urlsafe(32))
        session.add(row)
        await session.commit()
    return row.value


async def get_app_setting(session: AsyncSession, key: str) -> str | None:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    return None if row is None else row.value


async def set_app_setting(session: AsyncSession, key: str, value: str) -> None:
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await session.commit()
