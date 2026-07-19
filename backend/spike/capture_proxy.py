"""SPIKE (D1): logging pass-through proxy in front of the LiteLLM gateway.

Both approaches (Agno, hand-rolled) point their OpenAI-compatible client at
this proxy (port 54999) instead of LiteLLM (54000). Every request body is
logged VERBATIM to a JSONL file before forwarding, so the prompt-discipline
audit quotes exactly what each framework put on the wire. Streamed responses
are teed to extract the usage chunk (we inject stream_options.include_usage
into the FORWARDED copy only, never into the logged original, so Agno's
token accounting is measured even though Agno doesn't request usage itself).

Run:  uv run python spike/capture_proxy.py captures/agno.jsonl
"""

import json
import sys
import time
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

UPSTREAM = "http://localhost:54000"
LOG_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "captures/capture.jsonl")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

client = httpx.AsyncClient(base_url=UPSTREAM, timeout=httpx.Timeout(180.0, connect=10.0))


def _log(record: dict) -> None:
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


async def chat_completions(request: Request) -> Response:
    raw = await request.body()
    body = json.loads(raw)
    record = {
        "ts": time.time(),
        "model": body.get("model"),
        "stream": bool(body.get("stream")),
        "request_body": body,  # verbatim, pre-mutation
    }
    forward = dict(body)
    if forward.get("stream"):
        # measurement aid only; the logged request_body above is untouched
        forward.setdefault("stream_options", {"include_usage": True})
    headers = {"Authorization": request.headers.get("authorization", "")}

    if not forward.get("stream"):
        resp = await client.post("/v1/chat/completions", json=forward, headers=headers)
        try:
            data = resp.json()
            record["usage"] = data.get("usage")
            record["response_head"] = str(data.get("choices", [{}])[0])[:500]
        except ValueError:
            record["response_head"] = resp.text[:500]
        record["status"] = resp.status_code
        _log(record)
        return Response(resp.content, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type"))

    upstream_req = client.build_request(
        "POST", "/v1/chat/completions", json=forward, headers=headers
    )
    upstream = await client.send(upstream_req, stream=True)
    record["status"] = upstream.status_code

    async def tee():
        usage = None
        text_parts: list[str] = []
        first_token_ts = None
        try:
            async for line in upstream.aiter_lines():
                if line.startswith("data: "):
                    data = line.removeprefix("data: ").strip()
                    if data and data != "[DONE]":
                        try:
                            chunk = json.loads(data)
                            if chunk.get("usage"):
                                usage = chunk["usage"]
                            for ch in chunk.get("choices") or []:
                                delta = (ch.get("delta") or {}).get("content")
                                if delta:
                                    if first_token_ts is None:
                                        first_token_ts = time.time()
                                    text_parts.append(delta)
                                tc = (ch.get("delta") or {}).get("tool_calls")
                                if tc and first_token_ts is None:
                                    first_token_ts = time.time()
                        except ValueError:
                            pass
                yield (line + "\n").encode()
        finally:
            await upstream.aclose()
            record["usage"] = usage
            record["first_chunk_ts"] = first_token_ts
            record["response_head"] = "".join(text_parts)[:500]
            _log(record)

    return StreamingResponse(tee(), status_code=upstream.status_code,
                             media_type="text/event-stream")


app = Starlette(routes=[Route("/v1/chat/completions", chat_completions, methods=["POST"])])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=54999, log_level="warning")
