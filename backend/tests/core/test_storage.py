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


async def test_put_stream_roundtrips_and_uses_multipart_for_large_objects(
    storage: ObjectStorage,
) -> None:
    """put_stream is the upload path's escape from holding whole files in RAM
    (Phase 3 item 4), so it has to survive the case that motivated it: an
    object big enough to cross aioboto3's multipart threshold, where the
    single put_object path is not taken."""
    from io import BytesIO

    payload = b"ragz" * (3 * 1024 * 1024)  # 12 MB, above the 8 MB threshold
    await storage.put_stream("org/ws/doc/big.bin", BytesIO(payload))
    assert await storage.get("org/ws/doc/big.bin") == payload
    await storage.delete("org/ws/doc/big.bin")


async def test_put_stream_reads_from_the_current_position(storage: ObjectStorage) -> None:
    """Documented contract: the stream is read from where it is, not rewound
    for the caller. measure_upload seeks to 0 precisely because of this."""
    from io import BytesIO

    buf = BytesIO(b"skip-me:kept")
    buf.seek(len(b"skip-me:"))
    await storage.put_stream("org/ws/doc/partial.txt", buf, content_type="text/plain")
    assert await storage.get("org/ws/doc/partial.txt") == b"kept"
    await storage.delete("org/ws/doc/partial.txt")
