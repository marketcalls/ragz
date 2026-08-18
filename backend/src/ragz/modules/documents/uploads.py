"""Received-upload handling that never holds the whole file in memory.

Phase 3 item 4 of the 2026-08-17 architecture review. The upload route used to
accumulate the request body into a bytearray and then copy it again with
`bytes(buf)` -- two full copies resident at once, against a 100 MB
max_upload_mb, so a handful of concurrent uploads was a gigabyte of RSS and a
plausible way to OOM the API.

Nothing here buffers. Starlette has already spooled the body to a temporary
file by the time a route runs, so the file exists on disk and the only thing
that was ever gained by reading it into a bytes object was the size and the
digest -- both of which compute perfectly well one chunk at a time.
"""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

from ragz.core.errors import PayloadTooLarge

#: Chunk size for the measuring pass. Large enough that the loop is not the
#: bottleneck, small enough to stay off Python's large-allocation path.
_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class UploadedContent:
    """A file that has been received and measured but NOT held in memory.

    `stream` is positioned at 0 and is meant to be read exactly once, by the
    storage upload. `size_bytes` and `sha256` are already known, so the quota
    check and the duplicate-content lookup need no access to the bytes at all.
    """

    stream: BinaryIO
    size_bytes: int
    sha256: str

    @classmethod
    def from_bytes(cls, data: bytes) -> "UploadedContent":
        """For callers that legitimately already have the bytes -- small
        fixtures in tests, and the bot relay's inline attachments. Buffering is
        not a problem when the caller chose to hold the object in the first
        place; the route is the path where it was."""
        return cls(
            stream=BytesIO(data),
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )


async def measure_upload(file: object, *, max_bytes: int, limit_message: str) -> UploadedContent:
    """Stream a Starlette UploadFile once to get its size and digest, then
    rewind it for the storage upload.

    Aborts as soon as the running total passes `max_bytes`, so an oversized
    body costs one chunk of overshoot rather than the whole file -- the same
    early-abort the route did before, minus the accumulation.
    """
    digest = hashlib.sha256()
    size = 0
    read = file.read  # type: ignore[attr-defined]
    while chunk := await read(_CHUNK):
        size += len(chunk)
        if size > max_bytes:
            raise PayloadTooLarge(limit_message)
        digest.update(chunk)
    await file.seek(0)  # type: ignore[attr-defined]
    # .file is the underlying SpooledTemporaryFile; aioboto3 reads it directly.
    return UploadedContent(
        stream=file.file,  # type: ignore[attr-defined]
        size_bytes=size,
        sha256=digest.hexdigest(),
    )
