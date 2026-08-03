from pathlib import Path

import httpx
import pytest

from ragz.core.app_settings import set_app_setting
from ragz.core.config import Settings
from ragz.modules.documents.parsers import LlamaParseParser, parse_document
from ragz.modules.documents.pipeline import IngestFailure, PageBlock


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    from ragz.modules.secrets.crypto import ensure_kek
    kek = tmp_path / "kek"
    ensure_kek(str(kek))
    return Settings(_env_file=None, kek_file=str(kek))


def _llama_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/upload"):
            return httpx.Response(200, json={"id": "job1"})
        if p.endswith("/job/job1"):
            return httpx.Response(200, json={"status": "SUCCESS"})
        if p.endswith("/job/job1/result/json"):
            return httpx.Response(200, json={"pages": [
                {"page": 1, "md": "Hello"}, {"page": 2, "md": "World"},
            ]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_llamaparse_maps_pages_to_blocks() -> None:
    p = LlamaParseParser(api_key="llx-x", transport=_llama_transport())
    blocks = await p.parse(b"%PDF-1.4 ...", "doc.pdf")
    assert blocks == [
        PageBlock(page=1, text="Hello", kind="text"),
        PageBlock(page=2, text="World", kind="text"),
    ]


async def test_llamaparse_error_status_raises_ingest_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"id": "j"})
        return httpx.Response(200, json={"status": "ERROR"})

    p = LlamaParseParser(api_key="llx-x", transport=httpx.MockTransport(handler))
    with pytest.raises(IngestFailure):
        await p.parse(b"x", "doc.pdf")


async def test_llamaparse_malformed_result_body_raises_ingest_failure() -> None:
    # Upload + status poll succeed, but the job-result response has a
    # non-JSON body -> .json() raises json.JSONDecodeError (a ValueError).
    # This must surface as IngestFailure, not an unhandled worker exception.
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/upload"):
            return httpx.Response(200, json={"id": "job1"})
        if p.endswith("/job/job1"):
            return httpx.Response(200, json={"status": "SUCCESS"})
        if p.endswith("/job/job1/result/json"):
            return httpx.Response(200, text="not json")
        return httpx.Response(404)

    p = LlamaParseParser(api_key="llx-x", transport=httpx.MockTransport(handler))
    with pytest.raises(IngestFailure):
        await p.parse(b"x", "doc.pdf")


async def test_parse_document_docling_default(session, settings) -> None:
    # No app_setting -> docling path; a .txt round-trips through parse_bytes.
    blocks = await parse_document(
        session, settings, data=b"line one\n\nline two", filename="a.txt"
    )
    assert [b.text for b in blocks] == ["line one", "line two"]


async def test_parse_document_llamaparse_without_key_raises(session, settings) -> None:
    await set_app_setting(session, "document_parser", "llamaparse")
    with pytest.raises(IngestFailure):
        await parse_document(session, settings, data=b"x", filename="a.pdf")
