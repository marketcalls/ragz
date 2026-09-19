"""Bounded, network-free regression probe against the selected Ragz source."""
import asyncio
import json
import socket
from unittest.mock import patch

import httpx

from ragz.core.net import is_blocked_ip
from ragz.modules.chat import media, web_content


async def main():
    calls = []

    def resolve(host, *args, **kwargs):
        ip = "93.184.216.34" if host == "public.example" else "100.64.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    def respond(request):
        calls.append(request.url.host)
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"Location": "http://100.64.0.1/status"})
        return httpx.Response(200, text="Synthetic private-network marker", headers={"Content-Type": "text/plain"})

    with patch.object(socket, "getaddrinfo", resolve):
        result = await web_content.fetch_page_text("https://public.example/article", transport=httpx.MockTransport(respond))
        print(json.dumps({
            "source": media.__file__,
            "core_guard_blocks_cgnat": is_blocked_ip("100.64.0.1"),
            "media_guard_accepts_cgnat": media._resolve_safe("100.64.0.1") is not None,
            "mock_destinations": calls,
            "mock_private_text_returned": result == "Synthetic private-network marker",
        }, indent=2))


asyncio.run(main())
