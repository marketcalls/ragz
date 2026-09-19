import inspect
from collections.abc import AsyncIterator
from typing import Any

from aiobotocore.session import get_session
from botocore.exceptions import ClientError

from ragz.core.config import Settings
from ragz.core.errors import NotFoundError

# Bytes per multipart part, and the cutoff below which a single put_object is
# used instead. S3 requires every part but the last to be >= 5 MiB.
_PART_SIZE = 8 * 1024 * 1024


async def _read_chunk(fileobj: Any, size: int) -> bytes:
    """Read at most `size` bytes from a sync OR async file-like object."""
    chunk = fileobj.read(size)
    if inspect.isawaitable(chunk):
        chunk = await chunk
    return chunk or b""


async def _write_chunk(fileobj: Any, chunk: bytes) -> None:
    """Write one chunk to a sync OR async file-like object."""
    written = fileobj.write(chunk)
    if inspect.isawaitable(written):
        await written


class ObjectStorage:
    """Thin async S3 wrapper for MinIO. One bucket per deployment."""

    def __init__(self, *, endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._session = get_session()
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self.bucket = bucket

    def _client(self) -> Any:
        return self._session.create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )

    async def ensure_bucket(self) -> None:
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self.bucket)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"404", "NoSuchBucket"}:
                    await s3.create_bucket(Bucket=self.bucket)
                else:
                    raise

    async def head_bucket(self) -> None:
        """Lightweight existence check for health probes. Unlike
        `ensure_bucket`, this never creates the bucket as a side effect --
        a health check must only observe, not mutate."""
        async with self._client() as s3:
            await s3.head_bucket(Bucket=self.bucket)

    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        async with self._client() as s3:
            await s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def put_stream(
        self, key: str, fileobj: Any, content_type: str = "application/octet-stream"
    ) -> None:
        """Upload from a file-like object instead of a bytes blob.

        `put` needs the whole object resident before the first byte goes out,
        which at a large configured upload boundary can mean the whole object
        resident per concurrent request. This reads the stream one part at a time
        and switches to multipart past `_PART_SIZE`, so peak memory is bounded
        by the part size rather than by the file.

        Written against raw aiobotocore on purpose: boto3's `upload_fileobj`
        helper is not part of the aiobotocore client (it lives in boto3's
        S3Transfer), and aioboto3 -- which does provide it -- pins
        aiobotocore<3, which the pinned litellm build will not resolve with.

        `fileobj` only has to implement `read`; the result is awaited if it is
        awaitable, so a plain SpooledTemporaryFile (what Starlette hands us for
        an upload) works unwrapped. It is read from its current position --
        seek it where you want it before calling.
        """
        async with self._client() as s3:
            head = await _read_chunk(fileobj, _PART_SIZE)
            nxt = await _read_chunk(fileobj, _PART_SIZE)
            if not nxt:  # fits in one part: no multipart handshake needed
                await s3.put_object(
                    Bucket=self.bucket, Key=key, Body=head, ContentType=content_type
                )
                return
            started = await s3.create_multipart_upload(
                Bucket=self.bucket, Key=key, ContentType=content_type
            )
            upload_id = started["UploadId"]
            parts: list[dict[str, Any]] = []
            try:
                pending = [head, nxt]
                while pending:
                    chunk = pending.pop(0)
                    if not chunk:
                        break
                    uploaded = await s3.upload_part(
                        Bucket=self.bucket,
                        Key=key,
                        UploadId=upload_id,
                        PartNumber=len(parts) + 1,
                        Body=chunk,
                    )
                    parts.append({"ETag": uploaded["ETag"], "PartNumber": len(parts) + 1})
                    if not pending:
                        pending.append(await _read_chunk(fileobj, _PART_SIZE))
                await s3.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            except Exception:
                # Orphaned parts keep billing and block the key, so never leave
                # a half-finished upload behind.
                await s3.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
                raise

    async def get(self, key: str) -> bytes:
        async with self._client() as s3:
            try:
                obj = await s3.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"NoSuchKey", "404"}:
                    raise NotFoundError(f"object not found: {key}") from exc
                else:
                    raise
            body: bytes = await obj["Body"].read()
            return body

    async def download_to_fileobj(self, key: str, fileobj: Any) -> None:
        """Download into a writable file object while keeping peak RAM bounded."""

        async with self._client() as s3:
            try:
                obj = await s3.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"NoSuchKey", "404"}:
                    raise NotFoundError(f"object not found: {key}") from exc
                raise
            body = obj["Body"]
            while chunk := await body.read(_PART_SIZE):
                await _write_chunk(fileobj, chunk)

    async def get_prefix(self, key: str, max_bytes: int) -> bytes:
        """Read only the bounded prefix needed for server-side type checks."""

        async with self._client() as s3:
            try:
                obj = await s3.get_object(
                    Bucket=self.bucket,
                    Key=key,
                    Range=f"bytes=0-{max_bytes - 1}",
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"NoSuchKey", "404"}:
                    raise NotFoundError(f"object not found: {key}") from exc
                raise
            body: bytes = await obj["Body"].read()
            return body

    async def iter_bytes(
        self, key: str, chunk_size: int = 1024 * 1024
    ) -> AsyncIterator[bytes]:
        """Stream an object while keeping the S3 client alive for the body."""

        async with self._client() as s3:
            try:
                obj = await s3.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"NoSuchKey", "404"}:
                    raise NotFoundError(f"object not found: {key}") from exc
                raise
            body = obj["Body"]
            while chunk := await body.read(chunk_size):
                yield chunk

    async def iter_keys(self, prefix: str = "") -> AsyncIterator[str]:
        """List object keys incrementally for read-only reconciliation tools."""

        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    key = item.get("Key")
                    if isinstance(key, str):
                        yield key

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self.bucket, Key=key)  # S3 delete is idempotent


def build_storage(settings: Settings) -> ObjectStorage:
    return ObjectStorage(
        endpoint_url=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket=settings.minio_bucket,
    )
