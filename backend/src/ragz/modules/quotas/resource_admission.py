"""Atomic, durable reservations for storage and parser-work admission."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.db import naive_utc
from ragz.modules.quotas.models import ResourceReservation

_LOCK_SEED = 20260908
_RESERVATION_TTL = timedelta(hours=2)


@dataclass(frozen=True, slots=True)
class ReservationTotals:
    count: int
    size_bytes: int


async def lock_org(session: AsyncSession, org_id: UUID) -> None:
    """Serialize admission decisions for one organization until commit."""

    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(CAST(:org_id AS text), :seed))"
        ),
        {"org_id": str(org_id), "seed": _LOCK_SEED},
    )


async def prune_expired(session: AsyncSession) -> None:
    await session.execute(
        delete(ResourceReservation).where(ResourceReservation.expires_at <= naive_utc())
    )


async def totals(
    session: AsyncSession,
    *,
    org_id: UUID,
    kind: str,
    user_id: UUID | None = None,
) -> ReservationTotals:
    conditions = [
        ResourceReservation.org_id == org_id,
        ResourceReservation.kind == kind,
        ResourceReservation.expires_at > naive_utc(),
    ]
    if user_id is not None:
        conditions.append(ResourceReservation.user_id == user_id)
    count, size_bytes = (
        await session.execute(
            select(
                func.count(ResourceReservation.id),
                func.coalesce(func.sum(ResourceReservation.size_bytes), 0),
            ).where(*conditions)
        )
    ).one()
    return ReservationTotals(count=int(count), size_bytes=int(size_bytes))


def add(
    session: AsyncSession,
    *,
    org_id: UUID,
    user_id: UUID,
    kind: str,
    size_bytes: int,
) -> ResourceReservation:
    reservation = ResourceReservation(
        org_id=org_id,
        user_id=user_id,
        kind=kind,
        size_bytes=size_bytes,
        expires_at=naive_utc() + _RESERVATION_TTL,
    )
    session.add(reservation)
    return reservation


async def remove(session: AsyncSession, reservation_id: UUID) -> None:
    await session.execute(
        delete(ResourceReservation).where(ResourceReservation.id == reservation_id)
    )


async def release(session: AsyncSession, reservation_id: UUID) -> None:
    """Release after a failed/cancelled owner in a fresh transaction."""

    await remove(session, reservation_id)
    await session.commit()
