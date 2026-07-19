"""SPIKE (D1) Approach B: hand-rolled two-step agent loop (~100 lines of loop).

planner (JSON tool-call or direct answer) -> execute search -> synthesize with
the PRODUCTION system prompt + <data> discipline from modules/chat/prompting.
The synthesize stream reuses the production LiteLLMStreamer unchanged.

Usage:  uv run python spike/run_handrolled.py [model]
Requires capture_proxy.py on 54999 (captures/handrolled-<model>.jsonl).
"""

import asyncio
import json
import re
import sys

import httpx

from raghub.modules.chat.llm import LiteLLMStreamer, LLMDelta, LLMUsage
from raghub.modules.chat.prompting import SYSTEM_PROMPT

from spike_common import (
    PROXY_BASE,
    PROXY_KEY,
    QUESTIONS,
    RunRecord,
    login,
    now,
    save_records,
    search_documents,
)

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-5.6-luna"

PLANNER_PROMPT = (
    "You are the query planner for RagHub, a document-workspace assistant.\n"
    "Decide how to handle the user's message. Reply with EXACTLY ONE line of "
    "JSON, nothing else:\n"
    '- To search the workspace documents: {"action": "search", "query": "<search terms>"}\n'
    '- To answer directly (greetings/small talk only): {"action": "answer", "text": "<reply>"}\n'
    "Document questions MUST use search. Never answer a document question from memory."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


async def plan(client: httpx.AsyncClient, question: str) -> dict:
    resp = await client.post(
        "/chat/completions",
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": PLANNER_PROMPT},
                {"role": "user", "content": question},
            ],
        },
        headers={"Authorization": f"Bearer {PROXY_KEY}"},
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    match = _JSON_RE.search(content)
    if not match:
        return {"action": "answer", "text": content}
    try:
        return json.loads(match.group(0))
    except ValueError:
        return {"action": "answer", "text": content}


async def run_question(
    token: str, streamer: LiteLLMStreamer, qid: str, ws: str, question: str, expects_tool: bool
) -> RunRecord:
    rec = RunRecord(approach="handrolled", model=MODEL, qid=qid, question=question,
                    expects_tool=expects_tool)
    rec.t_start = now()
    parts: list[str] = []
    try:
        async with httpx.AsyncClient(base_url=PROXY_BASE, timeout=120.0) as client:
            decision = await plan(client, question)
        rec.llm_calls = 1
        if decision.get("action") == "search":
            rec.used_tool = True
            data_block = search_documents(token, ws, str(decision.get("query", question)))
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{data_block}\n\nQuestion: {question}"},
            ]
            async for item in streamer.stream(model=MODEL, messages=messages):
                if isinstance(item, LLMDelta):
                    if rec.t_first_final_token is None:
                        rec.t_first_final_token = now()
                    parts.append(item.text)
                elif isinstance(item, LLMUsage):
                    rec.notes.append(f"synth_usage p={item.prompt_tokens} c={item.completion_tokens}")
            rec.llm_calls = 2
        else:
            # planner answered directly; its completion IS the final answer
            rec.t_first_final_token = now()
            parts.append(str(decision.get("text", "")))
    except Exception as exc:
        rec.error = f"{type(exc).__name__}: {exc}"[:300]
    rec.t_end = now()
    rec.answer = "".join(parts)
    return rec


async def main() -> None:
    token = login()
    streamer = LiteLLMStreamer(base_url="http://localhost:54999", master_key=PROXY_KEY)
    records: list[RunRecord] = []
    for qid, ws, question, expects_tool in QUESTIONS:
        print(f"--- {qid}: {question}")
        rec = await run_question(token, streamer, qid, ws, question, expects_tool)
        lat = rec.first_token_latency
        print(f"    first_token={lat:.2f}s" if lat is not None else "    no tokens",
              f"tool={rec.used_tool} err={rec.error}")
        print(f"    answer: {rec.answer[:160]!r}")
        records.append(rec)
    save_records(f"results/handrolled-{MODEL.replace(':', '_').replace('/', '_')}.json", records)


if __name__ == "__main__":
    asyncio.run(main())
