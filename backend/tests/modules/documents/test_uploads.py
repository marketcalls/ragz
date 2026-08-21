"""measure_upload: the streaming replacement for the upload route's
read-into-bytearray-then-copy (Phase 3 item 4)."""

import hashlib
import tempfile

import pytest
from starlette.datastructures import UploadFile

from ragz.core.errors import PayloadTooLarge
from ragz.modules.documents.uploads import UploadedContent, measure_upload


def _upload(data: bytes, *, name: str = "f.bin") -> UploadFile:
    """A real Starlette UploadFile, spooled exactly as a request would give it
    to a route -- not a stand-in, so the .file/.seek/.read contract under test
    is the production one."""
    f = UploadFile(filename=name, file=tempfile.SpooledTemporaryFile())
    f.file.write(data)
    f.file.seek(0)
    return f


async def test_measures_size_and_digest_without_copying_the_payload() -> None:
    data = b"ragz" * 100_000  # 400 kB
    upload = _upload(data)
    content = await measure_upload(upload, max_bytes=10**9, limit_message="too big")

    assert content.size_bytes == len(data)
    assert content.sha256 == hashlib.sha256(data).hexdigest()
    # The point of the change, asserted by IDENTITY: what comes back is the
    # spooled file itself, so the payload was never copied. Checking the type
    # instead would not catch a regression -- a `BytesIO(await file.read())`
    # holds the whole file in memory and is neither bytes nor bytearray, so a
    # type assertion passes while the bug is back.
    assert content.stream is upload.file
    assert content.stream.read() == data


async def test_rewinds_so_the_storage_upload_sees_the_whole_file() -> None:
    """The measuring pass consumes the stream; without the seek, storage would
    upload zero bytes and the document would be silently empty."""
    data = b"first-byte-matters"
    content = await measure_upload(_upload(data), max_bytes=10**9, limit_message="too big")
    assert content.stream.tell() == 0
    assert content.stream.read() == data


async def test_aborts_as_soon_as_the_limit_is_passed() -> None:
    with pytest.raises(PayloadTooLarge, match="too big"):
        await measure_upload(
            _upload(b"x" * (3 * 1024 * 1024)), max_bytes=1024 * 1024, limit_message="too big"
        )


async def test_empty_upload_measures_as_empty() -> None:
    content = await measure_upload(_upload(b""), max_bytes=10**9, limit_message="too big")
    assert content.size_bytes == 0
    assert content.sha256 == hashlib.sha256(b"").hexdigest()


def test_from_bytes_matches_the_streaming_measurement() -> None:
    """Both constructors feed the same service path, so they must agree --
    otherwise a document's dedup hash would depend on which caller created it."""
    data = b"identical content"
    content = UploadedContent.from_bytes(data)
    assert content.size_bytes == len(data)
    assert content.sha256 == hashlib.sha256(data).hexdigest()
    assert content.stream.read() == data
