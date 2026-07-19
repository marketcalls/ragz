import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import ConflictError
from raghub.modules.tenancy import service as tenancy_service
from raghub.modules.tenancy.models import Organization


async def test_set_org_sso_domains_rejects_domain_claimed_by_other_org(
    session: AsyncSession,
) -> None:
    """Review finding: nothing stopped two orgs from claiming the same domain,
    which would silently route a JIT-provisioned user into whichever org
    `.first()` happened to sort first. The unique-claim invariant is enforced
    here, at the write path."""
    org_a = Organization(name="Acme-A")
    org_b = Organization(name="Acme-B")
    session.add_all([org_a, org_b])
    await session.flush()

    await tenancy_service.set_org_sso_domains(
        session, actor_id=None, org_id=org_a.id, domains=["acme.com"]
    )

    with pytest.raises(ConflictError):
        await tenancy_service.set_org_sso_domains(
            session, actor_id=None, org_id=org_b.id, domains=["acme.com"]
        )


async def test_set_org_sso_domains_conflict_does_not_leak_owning_org(
    session: AsyncSession,
) -> None:
    org_a = Organization(name="Acme-A")
    org_b = Organization(name="Acme-B")
    session.add_all([org_a, org_b])
    await session.flush()
    await tenancy_service.set_org_sso_domains(
        session, actor_id=None, org_id=org_a.id, domains=["acme.com"]
    )
    with pytest.raises(ConflictError) as exc_info:
        await tenancy_service.set_org_sso_domains(
            session, actor_id=None, org_id=org_b.id, domains=["acme.com"]
        )
    assert "Acme-A" not in str(exc_info.value)


async def test_set_org_sso_domains_allows_reclaiming_own_domain(
    session: AsyncSession,
) -> None:
    """Re-saving (or extending) the same org's own domain list must not trip the
    conflict check against itself."""
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()

    await tenancy_service.set_org_sso_domains(
        session, actor_id=None, org_id=org.id, domains=["acme.com"]
    )
    updated = await tenancy_service.set_org_sso_domains(
        session, actor_id=None, org_id=org.id, domains=["acme.com", "acme.io"]
    )
    assert updated.sso_domains == ["acme.com", "acme.io"]


async def test_set_org_sso_domains_partial_overlap_rejected(
    session: AsyncSession,
) -> None:
    """Even a single overlapping domain among several must be rejected -- not
    just an exact-set match."""
    org_a = Organization(name="Acme-A")
    org_b = Organization(name="Acme-B")
    session.add_all([org_a, org_b])
    await session.flush()

    await tenancy_service.set_org_sso_domains(
        session, actor_id=None, org_id=org_a.id, domains=["acme.com"]
    )

    with pytest.raises(ConflictError):
        await tenancy_service.set_org_sso_domains(
            session, actor_id=None, org_id=org_b.id, domains=["beta.com", "acme.com"]
        )
