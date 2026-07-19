"""SPIKE (D1) shared harness: login, live search tool, iron-rule-5 data blocks,
question set, and per-run measurement records.

The search tool calls the LIVE search route (the retrieval seam over HTTP) and
renders results with the SAME <data> discipline as modules/chat/prompting.py —
we import render-helpers straight from the module to prove reuse is possible.
"""

import json
import time
import urllib.request
from dataclasses import asdict, dataclass, field

from raghub.modules.chat.prompting import PromptSource, render_data_blocks

API = "http://localhost:8000/api/v1"
PROXY_BASE = "http://localhost:54999/v1"  # capture proxy -> LiteLLM
PROXY_KEY = "sk-raghub-dev-master"

WS_POLICY = "0a075912-7b03-4d48-b73b-0aa02250537f"   # planh-smoke: policy.pptx v1+v2, scanned.pdf
WS_FINANCE = "61222bb1-0f3f-41e7-90c5-c9f321f4a924"  # planf-smoke-f4: finance memo

# (id, workspace or None, question, expects_tool)
QUESTIONS: list[tuple[str, str | None, str, bool]] = [
    ("q1", WS_POLICY, "Where is the muster point?", True),
    ("q2", WS_POLICY, "Where is the assembly point according to the site notice?", True),
    ("q3", WS_POLICY, "What color are the emergency exits marked in?", True),
    ("q4", WS_POLICY, "What should I do during an evacuation?", True),
    ("q5", WS_FINANCE, "What is the budget code for the Zephyr project acquisition?", True),
    ("q6", WS_FINANCE, "Which project does budget code XKCD-7741-f4 belong to?", True),
    ("q7", WS_POLICY, "Who signs in at the site, according to the documents?", True),
    ("q8", WS_POLICY, "Hi there!", False),
    ("q9", WS_FINANCE, "Thanks, that's all for now.", False),
]


def _post(url: str, body: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def login() -> str:
    return _post(
        f"{API}/auth/login",
        {"email": "admin-f4@example.com", "password": "SmokePass123!x"},
    )["access_token"]


_DOC_NAMES: dict[str, str] = {}


def _doc_name(token: str, workspace_id: str, document_id: str) -> str:
    if document_id not in _DOC_NAMES:
        req = urllib.request.Request(
            f"{API}/workspaces/{workspace_id}/documents",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req) as resp:
            for d in json.load(resp):
                _DOC_NAMES[d["id"]] = d["filename"]
    return _DOC_NAMES.get(document_id, "unknown")


def search_documents(token: str, workspace_id: str, query: str, top_k: int = 4) -> str:
    """The one tool. Hits the live ACL-filtered search route and returns the
    top chunks wrapped in <data> blocks (iron rule 5 discipline, escaped
    attrs, via the production render function)."""
    result = _post(
        f"{API}/workspaces/{workspace_id}/search",
        {"query": query, "top_k": top_k},
        token,
    )
    if result["no_answer"] or not result["chunks"]:
        return "No sufficiently relevant document excerpts were found."
    sources = [
        PromptSource(
            marker=i + 1,
            filename=f"{_doc_name(token, workspace_id, c['document_id'])} (v{c['version']})",
            page=c["page"],
            text=c["text"],
            section=c.get("section"),
        )
        for i, c in enumerate(result["chunks"])
    ]
    return render_data_blocks(sources)


@dataclass
class RunRecord:
    approach: str
    model: str
    qid: str
    question: str
    expects_tool: bool
    t_start: float = 0.0
    t_first_final_token: float | None = None  # first content token of FINAL answer
    t_end: float = 0.0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    used_tool: bool = False
    answer: str = ""
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def first_token_latency(self) -> float | None:
        if self.t_first_final_token is None:
            return None
        return self.t_first_final_token - self.t_start

    def to_json(self) -> dict:
        d = asdict(self)
        d["first_token_latency_s"] = self.first_token_latency
        d["total_wall_s"] = self.t_end - self.t_start
        return d


def save_records(path: str, records: list[RunRecord]) -> None:
    from pathlib import Path

    path = str(Path(__file__).parent / path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump([r.to_json() for r in records], f, indent=2)
    print(f"wrote {len(records)} records -> {path}")


def now() -> float:
    return time.time()
