"""sec RAGZ-PUB-03 (bounded slice): per-org document-count + storage-byte
quota, enforced at upload BEFORE the new document row is created or any
bytes are stored (modules/documents/service.py::_enforce_org_upload_quota).

Mirrors test_documents_routes.py's fixtures/helpers (auth, make_workspace,
captured_enqueues) and test_oversized_upload_413's env-var + cache-clear
pattern for setting config per test.
"""

from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.config import get_settings
from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.documents.models import Document
from ragz.modules.tenancy.models import Organization
from tests.api.test_documents_routes import auth, captured_enqueues, make_workspace  # noqa: F401


@pytest.fixture
def quota_settings(
    stack_env: None, monkeypatch: pytest.MonkeyPatch
):  # type: ignore[type-arg]
    """Sets the two quota env vars and clears the cached Settings so the next
    get_settings() call (inside the upload path) picks them up -- same
    pattern as test_oversized_upload_413."""

    def _set(*, max_documents: int = 0, max_storage_bytes: int = 0) -> None:
        monkeypatch.setenv("RAGZ_ORG_MAX_DOCUMENTS", str(max_documents))
        monkeypatch.setenv("RAGZ_ORG_MAX_STORAGE_BYTES", str(max_storage_bytes))
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


@pytest.fixture
async def storage_put_calls(monkeypatch: pytest.MonkeyPatch) -> list:  # type: ignore[type-arg]
    """Records every ObjectStorage write (any instance, either API) -- proves a
    rejected upload never reaches storage (no orphaned MinIO object).

    Both `put` and `put_stream` are recorded on purpose. The upload route moved
    to put_stream when uploads stopped being buffered, and a spy on `put` alone
    silently recorded nothing afterwards -- the assertions below still ran, but
    against an empty list, so they could no longer fail. Watching every write
    method keeps the guarantee attached to "did anything reach storage" rather
    than to whichever method the route happens to call today."""
    calls: list = []  # type: ignore[type-arg]
    from ragz.core.storage import ObjectStorage

    original_put = ObjectStorage.put
    original_put_stream = ObjectStorage.put_stream

    async def _spy_put(self, key, data, content_type="application/octet-stream"):  # type: ignore[no-untyped-def]
        calls.append(key)
        return await original_put(self, key, data, content_type=content_type)

    async def _spy_put_stream(self, key, fileobj, content_type="application/octet-stream"):  # type: ignore[no-untyped-def]
        calls.append(key)
        return await original_put_stream(self, key, fileobj, content_type=content_type)

    monkeypatch.setattr(ObjectStorage, "put", _spy_put)
    monkeypatch.setattr(ObjectStorage, "put_stream", _spy_put_stream)
    return calls


async def test_document_count_quota_rejects_over_limit(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    quota_settings, captured_enqueues: dict, storage_put_calls: list,  # type: ignore[type-arg]  # noqa: F811
) -> None:
    quota_settings(max_documents=1)
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)

    r1 = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("one.txt", b"first document", "text/plain")},
    )
    assert r1.status_code == 201

    r2 = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("two.txt", b"second document", "text/plain")},
    )
    assert r2.status_code == 413
    assert "document limit" in r2.json()["detail"]

    docs = list(
        (await session.execute(select_documents(ws_id))).scalars()
    )
    assert len(docs) == 1
    assert docs[0].filename == "one.txt"
    # the rejected upload never reached storage.put
    assert len(storage_put_calls) == 1  # only the first (accepted) upload
    assert len(captured_enqueues["ingest"]) == 1


async def test_storage_bytes_quota_rejects_over_limit(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    quota_settings, captured_enqueues: dict, storage_put_calls: list,  # type: ignore[type-arg]  # noqa: F811
) -> None:
    quota_settings(max_storage_bytes=10)
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)

    # fits (5 bytes <= 10 byte cap)
    r1 = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("small.txt", b"12345", "text/plain")},
    )
    assert r1.status_code == 201

    # would push total to 5 + 6 = 11 > 10 -> rejected
    r2 = await client.post(
        f"/api/v1/workspaces/{ws_id}/documents", headers=h,
        files={"file": ("big.txt", b"123456", "text/plain")},
    )
    assert r2.status_code == 413
    assert "storage limit" in r2.json()["detail"]

    docs = list((await session.execute(select_documents(ws_id))).scalars())
    assert len(docs) == 1
    assert docs[0].filename == "small.txt"
    assert len(storage_put_calls) == 1
    assert len(captured_enqueues["ingest"]) == 1


async def test_zero_quotas_unrestricted(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    quota_settings, captured_enqueues: dict,  # type: ignore[type-arg]  # noqa: F811
) -> None:
    """Default (both 0) -- unlimited, unchanged behavior."""
    quota_settings(max_documents=0, max_storage_bytes=0)
    h = await auth(client, "a@acme.com")
    ws_id = await make_workspace(client, h)

    for i in range(3):
        r = await client.post(
            f"/api/v1/workspaces/{ws_id}/documents", headers=h,
            files={"file": (f"doc{i}.txt", f"contents {i}".encode(), "text/plain")},
        )
        assert r.status_code == 201

    docs = list((await session.execute(select_documents(ws_id))).scalars())
    assert len(docs) == 3


async def test_quota_is_per_org_not_global(
    client: httpx.AsyncClient, seeded_user: User, session: AsyncSession,
    quota_settings, captured_enqueues: dict,  # type: ignore[type-arg]  # noqa: F811
) -> None:
    """Org A hitting its document-count cap must not block org B."""
    quota_settings(max_documents=1)

    org_b = Organization(name="quotaOrgB")
    session.add(org_b)
    await session.flush()
    user_b = User(org_id=org_b.id, email="b@orgb.example",
                  password_hash=hash_password("pw123456"), role="admin")
    session.add(user_b)
    await session.commit()

    h_a = await auth(client, "a@acme.com")
    ws_a = await make_workspace(client, h_a)
    r_a1 = await client.post(
        f"/api/v1/workspaces/{ws_a}/documents", headers=h_a,
        files={"file": ("a1.txt", b"org a doc", "text/plain")},
    )
    assert r_a1.status_code == 201
    # org A is now at its cap of 1
    r_a2 = await client.post(
        f"/api/v1/workspaces/{ws_a}/documents", headers=h_a,
        files={"file": ("a2.txt", b"org a doc 2", "text/plain")},
    )
    assert r_a2.status_code == 413

    h_b = await auth(client, "b@orgb.example")
    ws_b = await make_workspace(client, h_b)
    r_b1 = await client.post(
        f"/api/v1/workspaces/{ws_b}/documents", headers=h_b,
        files={"file": ("b1.txt", b"org b doc", "text/plain")},
    )
    assert r_b1.status_code == 201


def select_documents(workspace_id: str):  # type: ignore[no-untyped-def]
    return select(Document).where(Document.workspace_id == UUID(workspace_id))
