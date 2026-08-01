import pytest
from botocore.exceptions import ClientError

from ragz.core.errors import NotFoundError
from ragz.core.storage import ObjectStorage


async def test_put_get_delete_roundtrip(storage: ObjectStorage) -> None:
    await storage.put("org/ws/doc/file.txt", b"hello ragz", content_type="text/plain")
    assert await storage.get("org/ws/doc/file.txt") == b"hello ragz"
    await storage.delete("org/ws/doc/file.txt")
    with pytest.raises(NotFoundError):
        await storage.get("org/ws/doc/file.txt")


async def test_delete_missing_is_idempotent(storage: ObjectStorage) -> None:
    await storage.delete("does/not/exist")  # no raise


async def test_ensure_bucket_idempotent(storage: ObjectStorage) -> None:
    await storage.ensure_bucket()
    await storage.ensure_bucket()


async def test_get_with_wrong_credentials_raises_client_error(
    minio_config: dict[str, str],
) -> None:
    """Verify that authentication errors raise ClientError, not NotFoundError."""
    bad_storage = ObjectStorage(
        endpoint_url=minio_config["endpoint"],
        access_key="wrong-access-key",
        secret_key="wrong-secret-key",  # noqa: S106
        bucket="ragz-test",
    )
    # Try to get with bad credentials - should raise ClientError, not NotFoundError
    with pytest.raises(ClientError):
        await bad_storage.get("any-key")
