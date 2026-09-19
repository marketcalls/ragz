from collections.abc import AsyncIterator
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.auth.models import User
from ragz.modules.tenancy.models import Group, UserGroup, WorkspaceMember
from tests.api.test_permissions_routes import make_templated_member


async def auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_workspace(client: httpx.AsyncClient, h: dict[str, str]) -> str:
    r = await client.post("/api/v1/workspaces", json={"name": "Docs"}, headers=h)
    return str(r.json()["id"])


async def upload(
    client: httpx.AsyncClient, h: dict[str, str], ws_id: str,
    filename: str = "notes.txt", content: bytes = b"the flux capacitor hums",
    content_type: str = "text/plain",
) -> dict:  # type: ignore[type-arg]
    r = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": (filename, content, content_type)},
    )
    assert r.status_code == 201
    return r.json()  # type: ignore[no-any-return]


async def test_member_can_fetch_file_bytes(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    doc = await upload(client, h, ws_id, "notes.txt", b"the flux capacitor hums")

    r = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h)
    assert r.status_code == 200
    assert r.content == b"the flux capacitor hums"
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["content-disposition"] == 'inline; filename="notes.txt"'


async def test_non_group_member_denied_restricted_document_bytes(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, stack_env: None,
) -> None:
    """The key ACL test: a plain member who is a workspace member (and can
    therefore SEE the restricted document in listings) but is NOT in its ACL
    group must be denied the file bytes -- non-leaking (same error class the
    service layer already uses for restricted-document access), and no bytes
    returned."""
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    doc = await upload(client, h_admin, ws_id, "secret.txt", b"the acquisition price is 4400")

    group = Group(org_id=seeded_user.org_id, name="finance")
    session.add(group)
    await session.commit()
    r_acl = await client.put(
        f"/api/v1/documents/{doc['id']}/acl", headers=h_admin,
        json={"acl_group_ids": [str(group.id)]},
    )
    assert r_acl.status_code == 200

    outsider = User(org_id=seeded_user.org_id, email="out@acme.com",
                     password_hash=seeded_user.password_hash, role="user")
    session.add(outsider)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=UUID(ws_id), user_id=outsider.id))
    await session.commit()

    h_out = await auth(client, "out@acme.com")

    # Existence is still visible in the listing (Drive-style)...
    listing = await client.get(f"/api/v1/workspaces/{ws_id}/documents", headers=h_out)
    assert doc["id"] in [d["id"] for d in listing.json()]

    # ...but the content endpoint must deny it and return no bytes.
    r = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h_out)
    assert r.status_code in (403, 404)
    assert r.content != b"the acquisition price is 4400"
    assert b"4400" not in r.content


async def test_group_member_can_fetch_restricted_document_bytes(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, stack_env: None,
) -> None:
    """Positive control for the ACL test above: a member who IS in the
    document's ACL group gets the bytes."""
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    doc = await upload(client, h_admin, ws_id, "secret.txt", b"the acquisition price is 4400")

    group = Group(org_id=seeded_user.org_id, name="finance")
    session.add(group)
    await session.commit()
    r_acl = await client.put(
        f"/api/v1/documents/{doc['id']}/acl", headers=h_admin,
        json={"acl_group_ids": [str(group.id)]},
    )
    assert r_acl.status_code == 200

    member = User(org_id=seeded_user.org_id, email="in@acme.com",
                  password_hash=seeded_user.password_hash, role="user")
    session.add(member)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=UUID(ws_id), user_id=member.id))
    session.add(UserGroup(group_id=group.id, user_id=member.id))
    await session.commit()

    h_in = await auth(client, "in@acme.com")
    r = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h_in)
    assert r.status_code == 200
    assert r.content == b"the acquisition price is 4400"


async def test_role_denied_content_read_action_gets_403(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession, stack_env: None,
) -> None:
    """Route-policy/enforcement gate: a custom role WITHOUT
    documents.content.read (but WITH workspace membership) must be denied
    even though it could reach get_document_checked/user_can_access_document
    successfully -- require_action is the outer gate."""
    h_admin = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h_admin)
    doc = await upload(client, h_admin, ws_id)

    await make_templated_member(
        session, seeded_user, email="norights@acme.com", template_name="NoContentRead",
        permissions=["documents.list"], workspace_id=ws_id,
    )
    h = await auth(client, "norights@acme.com")
    r = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h)
    assert r.status_code == 403


async def test_unknown_document_id_returns_404(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
) -> None:
    h = await auth(client, "a@acme.com")
    r = await client.get(
        "/api/v1/documents/00000000-0000-0000-0000-000000000000/file", headers=h
    )
    assert r.status_code == 404


async def test_active_html_is_download_only_even_when_stored_with_active_mime(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    body = b"<!doctype html><script>parent.previewPwned=true</script>"
    doc = await upload(client, h, ws_id, "legacy.html", body, "text/html")

    response = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h)

    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_verified_pdf_remains_inline_and_page_previewable(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    body = b"%PDF-1.7\n% synthetic harmless preview fixture\n%%EOF\n"
    doc = await upload(client, h, ws_id, "manual.pdf", body, "text/html")

    response = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h)

    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_file_response_streams_after_a_bounded_signature_read(
    client: httpx.AsyncClient,
    seeded_user: User,
    stack_env: None,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from ragz.core.storage import ObjectStorage

    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    body = b"%PDF-1.7\nstreamed fixture\n%%EOF\n"
    doc = await upload(client, h, ws_id, "streamed.pdf", body, "application/pdf")

    async def _forbid_buffered_get(self, key):  # type: ignore[no-untyped-def]
        raise AssertionError("document route buffered the whole object")

    async def _prefix(self, key, max_bytes):  # type: ignore[no-untyped-def]
        assert max_bytes == 8192
        return body[:max_bytes]

    async def _stream(self, key, chunk_size=1024 * 1024) -> AsyncIterator[bytes]:  # type: ignore[no-untyped-def]
        assert chunk_size == 1024 * 1024
        yield body[:9]
        yield body[9:]

    monkeypatch.setattr(ObjectStorage, "get", _forbid_buffered_get)
    monkeypatch.setattr(ObjectStorage, "get_prefix", _prefix, raising=False)
    monkeypatch.setattr(ObjectStorage, "iter_bytes", _stream, raising=False)

    response = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h)

    assert response.status_code == 200
    assert response.content == body


async def test_unicode_filename_uses_an_ascii_fallback_and_rfc5987_value(
    client: httpx.AsyncClient, seeded_user: User, stack_env: None,
) -> None:
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)
    doc = await upload(client, h, ws_id, "契約📄.txt", b"terms", "text/plain")

    response = await client.get(f"/api/v1/documents/{doc['id']}/file", headers=h)

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert 'filename="download.txt"' in disposition
    assert "filename*=UTF-8''%E5%A5%91%E7%B4%84%F0%9F%93%84.txt" in disposition
