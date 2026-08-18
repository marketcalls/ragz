"""The DATABASE must refuse a cross-tenant reference.

Architecture review P1 "Postgres tenant isolation is application-convention
based": documents/chats/folders carried only single-column FKs on workspace_id
and user_id. Those prove the referenced row EXISTS but say nothing about whose
it is, so nothing in the database stopped a row in org A pointing at a workspace
in org B -- the only thing preventing it was every query path remembering to go
through TenantContext.

These tests bypass the service layer ON PURPOSE. TenantContext is well tested
elsewhere; what is under test here is what happens when application-level
discipline FAILS. A bug, a migration script, or a psql session must not be able
to persist a cross-tenant row.

This is defence in depth, not a replacement for TenantContext: it turns a whole
class of bug from "unlikely to be written" into "impossible to persist".
"""

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.chat.models import Chat
from ragz.modules.documents.models import Document, Folder
from ragz.modules.tenancy.models import Organization, Workspace


async def _two_orgs(session: AsyncSession):  # type: ignore[no-untyped-def]
    """Two orgs, each with its own workspace and user."""
    org_a, org_b = Organization(name="A"), Organization(name="B")
    session.add_all([org_a, org_b])
    await session.flush()
    ws_a = Workspace(org_id=org_a.id, name="wsA")
    ws_b = Workspace(org_id=org_b.id, name="wsB")
    user_a = User(org_id=org_a.id, email=f"a{uuid4().hex[:6]}@a.com",
                  password_hash="x", role="user")  # noqa: S106
    session.add_all([ws_a, ws_b, user_a])
    await session.commit()
    return org_a, org_b, ws_a, ws_b, user_a


async def test_a_document_cannot_reference_another_orgs_workspace(
    session: AsyncSession,
) -> None:
    org_a, _org_b, _ws_a, ws_b, user_a = await _two_orgs(session)

    session.add(
        Document(
            org_id=org_a.id,
            workspace_id=ws_b.id,  # <-- the other org's workspace
            filename="leak.pdf", mime="application/pdf", size_bytes=1,
            content_hash=uuid4().hex, storage_key="k",
            created_by=user_a.id, lineage_id=uuid4(),
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_document_cannot_be_created_by_another_orgs_user(
    session: AsyncSession,
) -> None:
    org_a, org_b, ws_a, _ws_b, _user_a = await _two_orgs(session)
    user_b = User(org_id=org_b.id, email=f"b{uuid4().hex[:6]}@b.com",
                  password_hash="x", role="user")  # noqa: S106
    session.add(user_b)
    await session.commit()

    session.add(
        Document(
            org_id=org_a.id, workspace_id=ws_a.id,
            filename="leak.pdf", mime="application/pdf", size_bytes=1,
            content_hash=uuid4().hex, storage_key="k",
            created_by=user_b.id,  # <-- the other org's user
            lineage_id=uuid4(),
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_chat_cannot_reference_another_orgs_workspace(
    session: AsyncSession,
) -> None:
    org_a, _org_b, _ws_a, ws_b, user_a = await _two_orgs(session)

    session.add(Chat(org_id=org_a.id, workspace_id=ws_b.id, user_id=user_a.id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_folder_cannot_reference_another_orgs_workspace(
    session: AsyncSession,
) -> None:
    org_a, _org_b, _ws_a, ws_b, user_a = await _two_orgs(session)

    session.add(
        Folder(org_id=org_a.id, workspace_id=ws_b.id, name="f", created_by=user_a.id)
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_the_same_tenant_case_still_works(session: AsyncSession) -> None:
    """Not a vacuous pass: the identical insert within ONE org must succeed."""
    org_a, _org_b, ws_a, _ws_b, user_a = await _two_orgs(session)

    doc = Document(
        org_id=org_a.id, workspace_id=ws_a.id,
        filename="fine.pdf", mime="application/pdf", size_bytes=1,
        content_hash=uuid4().hex, storage_key="k",
        created_by=user_a.id, lineage_id=uuid4(),
    )
    session.add(doc)
    session.add(Chat(org_id=org_a.id, workspace_id=ws_a.id, user_id=user_a.id))
    session.add(
        Folder(org_id=org_a.id, workspace_id=ws_a.id, name="ok", created_by=user_a.id)
    )
    await session.commit()
    assert doc.id is not None


async def test_a_document_cannot_be_filed_under_another_orgs_folder(
    session: AsyncSession,
) -> None:
    """Cubic P1: folder_id was not paired with org_id.

    e4f7c1a83b26 paired workspace_id and created_by but left the folder
    references single-column, so a bypassed write could still file one org's
    document under another org's folder.
    """
    org_a, org_b, ws_a, _ws_b, user_a = await _two_orgs(session)
    user_b = User(org_id=org_b.id, email=f"b{uuid4().hex[:6]}@b.com",
                  password_hash="x", role="user")  # noqa: S106
    session.add(user_b)
    await session.flush()
    ws_b_folder = Folder(org_id=org_b.id, workspace_id=_ws_b.id, name="theirs",
                         created_by=user_b.id)
    session.add(ws_b_folder)
    await session.commit()

    session.add(
        Document(
            org_id=org_a.id, workspace_id=ws_a.id,
            filename="leak.pdf", mime="application/pdf", size_bytes=1,
            content_hash=uuid4().hex, storage_key="k",
            created_by=user_a.id, lineage_id=uuid4(),
            folder_id=ws_b_folder.id,  # <-- the other org's folder
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_folder_cannot_nest_under_another_orgs_folder(
    session: AsyncSession,
) -> None:
    """A cross-tenant parent would also let one org's delete cascade into
    another org's subtree."""
    org_a, org_b, ws_a, _ws_b, user_a = await _two_orgs(session)
    user_b = User(org_id=org_b.id, email=f"b{uuid4().hex[:6]}@b.com",
                  password_hash="x", role="user")  # noqa: S106
    session.add(user_b)
    await session.flush()
    parent_b = Folder(org_id=org_b.id, workspace_id=_ws_b.id, name="parent",
                      created_by=user_b.id)
    session.add(parent_b)
    await session.commit()

    session.add(
        Folder(org_id=org_a.id, workspace_id=ws_a.id, name="child",
               created_by=user_a.id, parent_folder_id=parent_b.id)
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
