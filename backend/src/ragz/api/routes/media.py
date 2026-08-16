"""Public image proxy (generative UI Task 7). PUBLIC route -- see
`modules/chat/media.py`'s module docstring for why: `<img src>` cannot carry
a Bearer header, so the signed, time-limited `image_ref` IS the sole
authorization gate (a capability URL, mirroring a signed S3 URL). No
TenantContext dependency here by design (`api/policy.py::PUBLIC_ROUTES`)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from ragz.core.config import Settings, get_settings
from ragz.core.errors import NotFoundError
from ragz.modules.chat import media

SettingsDep = Annotated[Settings, Depends(get_settings)]

router = APIRouter(tags=["media"])

# Iron rule 5 / defense-in-depth: even though `fetch_image_safely` already
# re-encodes to WEBP (stripping any executable/active content the original
# bytes might have carried), the response still gets an extra-strict CSP so
# a browser never has a reason to sniff/execute the bytes as anything other
# than a passive image. `X-Content-Type-Options: nosniff` is NOT repeated
# here -- `SecurityHeadersMiddleware` (api/app.py) already appends it (plus
# its own CSP) to every response app-wide; setting it again here would only
# produce a duplicate `nosniff, nosniff` header value.
_RESPONSE_HEADERS = {
    "Cache-Control": "private, max-age=86400",
    "Content-Security-Policy": "default-src 'none'",
}


@router.get("/media/image/{ref}")
async def get_proxied_image(ref: str, request: Request, settings: SettingsDep) -> Response:
    data = await media.get_or_fetch_image(
        ref,
        redis=request.app.state.redis,
        settings=settings,
        now=media._now(),
        transport=getattr(request.app.state, "image_transport", None),
    )
    if data is None:
        raise NotFoundError("image not available")
    content, content_type = data
    return Response(content=content, media_type=content_type, headers=_RESPONSE_HEADERS)
