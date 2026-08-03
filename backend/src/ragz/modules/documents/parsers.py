"""Pluggable document parser seam. `document_parser` app_setting selects the
backend: `docling` (default, self-hosted, byte-identical to the prior direct
parse_bytes call) or `llamaparse` (LlamaIndex cloud via REST, no SDK). Both
return list[PageBlock] so the chunk/embed pipeline downstream is unchanged."""

import asyncio

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.core.app_settings import get_app_setting
from ragz.core.config import Settings
from ragz.core.errors import NotFoundError
from ragz.modules.documents.pipeline import IngestFailure, PageBlock, parse_bytes
from ragz.modules.secrets import service as secrets_service

_LLAMA_BASE = "https://api.cloud.llamaindex.ai/api/v1/parsing"
_LLAMA_POLL_INTERVAL = 3.0
_LLAMA_MAX_POLLS = 100  # ~5 min at 3s


class LlamaParseParser:
    """SDK-free LlamaParse client: upload -> poll job -> fetch JSON result,
    mapping each page to a PageBlock. Any failure raises IngestFailure so
    run_parse fails the document with a clear, user-visible message."""

    def __init__(
        self, *, api_key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._api_key = api_key
        self._transport = transport

    async def parse(self, data: bytes, filename: str) -> list[PageBlock]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(
                base_url=_LLAMA_BASE, timeout=60.0, transport=self._transport,
                headers=headers,
            ) as client:
                up = await client.post(
                    "/upload", files={"file": (filename, data)}
                )
                up.raise_for_status()
                job_id = str(up.json()["id"])
                for _ in range(_LLAMA_MAX_POLLS):
                    st = await client.get(f"/job/{job_id}")
                    st.raise_for_status()
                    status = st.json().get("status")
                    if status == "SUCCESS":
                        break
                    if status == "ERROR":
                        raise IngestFailure("LlamaParse reported an error parsing the file")
                    await asyncio.sleep(_LLAMA_POLL_INTERVAL)
                else:
                    raise IngestFailure("LlamaParse timed out")
                res = await client.get(f"/job/{job_id}/result/json")
                res.raise_for_status()
                pages = res.json().get("pages", [])
        except httpx.HTTPError as exc:
            raise IngestFailure("LlamaParse request failed") from exc
        blocks = [
            PageBlock(page=int(pg.get("page", i + 1)),
                      text=(pg.get("md") or pg.get("text") or "").strip(),
                      kind="text")
            for i, pg in enumerate(pages)
        ]
        blocks = [b for b in blocks if b.text]
        if not blocks:
            raise IngestFailure("LlamaParse returned no extractable text")
        return blocks


async def parse_document(
    session: AsyncSession, settings: Settings, *, data: bytes, filename: str
) -> list[PageBlock]:
    parser = await get_app_setting(session, "document_parser")
    if parser == "llamaparse":
        try:
            key = await secrets_service._get_secret_decrypted(  # noqa: SLF001
                session, name="llamaparse_api_key", settings=settings
            )
        except NotFoundError as exc:
            raise IngestFailure(
                "LlamaParse selected but no API key is configured"
            ) from exc
        return await LlamaParseParser(api_key=key).parse(data, filename)
    # docling default — byte-identical to the prior direct call.
    return await asyncio.to_thread(
        parse_bytes, data, filename,
        ocr_enabled=settings.ocr_enabled,
        ocr_min_chars_per_page=settings.ocr_min_chars_per_page,
    )
