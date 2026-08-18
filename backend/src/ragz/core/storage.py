from typing import Any

import aioboto3
from botocore.exceptions import ClientError

from ragz.core.config import Settings
from ragz.core.errors import NotFoundError


class ObjectStorage:
    """Thin async S3 wrapper for MinIO (aioboto3). One bucket per deployment."""

    def __init__(self, *, endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._session = aioboto3.Session()
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self.bucket = bucket

    def _client(self) -> Any:
        return self._session.client(
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
        which for a 100 MB upload (the max_upload_mb default) means 100 MB of
        RSS per concurrent request. upload_fileobj reads incrementally and
        switches to multipart above the transfer threshold, so peak memory is
        bounded by that threshold rather than by the file.

        `fileobj` only has to implement `read` returning bytes; aioboto3 awaits
        the result if it is awaitable, so a plain SpooledTemporaryFile (what
        Starlette hands us for an upload) works unwrapped. It is read from its
        current position -- seek it where you want it before calling.
        """
        async with self._client() as s3:
            await s3.upload_fileobj(
                fileobj, self.bucket, key, ExtraArgs={"ContentType": content_type}
            )

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
