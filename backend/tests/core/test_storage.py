import pytest

from raghub.core.errors import NotFoundError
from raghub.core.storage import ObjectStorage


async def test_put_get_delete_roundtrip(storage: ObjectStorage) -> None:
    await storage.put("org/ws/doc/file.txt", b"hello raghub", content_type="text/plain")
    assert await storage.get("org/ws/doc/file.txt") == b"hello raghub"
    await storage.delete("org/ws/doc/file.txt")
    with pytest.raises(NotFoundError):
        await storage.get("org/ws/doc/file.txt")


async def test_delete_missing_is_idempotent(storage: ObjectStorage) -> None:
    await storage.delete("does/not/exist")  # no raise


async def test_ensure_bucket_idempotent(storage: ObjectStorage) -> None:
    await storage.ensure_bucket()
    await storage.ensure_bucket()
