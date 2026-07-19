"""SPIKE (D1) analysis: join results/*.json (per-question timing windows) with
captures/*.jsonl (per-LLM-call request/usage logs from the capture proxy) and
print the raw data table + p50/p95 summaries as markdown.

Usage: uv run python spike/analyze.py results/agno-gpt-5.6-luna.json captures/agno-luna.jsonl ...
       (pairs of results,captures files)
"""

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).parent


def load_pairs(args: list[str]) -> list[tuple[list[dict], list[dict]]]:
    pairs = []
    for i in range(0, len(args), 2):
        results = json.loads((HERE / args[i]).read_text())
        captures = [json.loads(l) for l in (HERE / args[i + 1]).read_text().splitlines()]
        pairs.append((results, captures))
    return pairs


def pctl(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    k = (len(values) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def main() -> None:
    for results, captures in load_pairs(sys.argv[1:]):
        approach = results[0]["approach"]
        model = results[0]["model"]
        print(f"\n## {approach} / {model}\n")
        print("| q | tool | calls | prompt_tok | compl_tok | first_token_s | wall_s | err |")
        print("|---|------|-------|-----------|-----------|---------------|--------|-----|")
        lat_all, lat_tool, tok_totals, call_counts = [], [], [], []
        for r in results:
            # ts is logged at request receipt, strictly inside the question's
            # wall-clock window; no slop, or back-to-back questions double-count.
            window = [c for c in captures if r["t_start"] <= c["ts"] <= r["t_end"]]
            calls = len(window)
            pt = sum((c.get("usage") or {}).get("prompt_tokens", 0) for c in window)
            ct = sum((c.get("usage") or {}).get("completion_tokens", 0) for c in window)
            lat = r["first_token_latency_s"]
            if lat is not None:
                lat_all.append(lat)
                if r["expects_tool"]:
                    lat_tool.append(lat)
            tok_totals.append(pt + ct)
            call_counts.append(calls)
            print(f"| {r['qid']} | {r['used_tool']} | {calls} | {pt} | {ct} | "
                  f"{lat:.2f} | {r['total_wall_s']:.2f} | {r['error'] or ''} |"
                  if lat is not None else
                  f"| {r['qid']} | {r['used_tool']} | {calls} | {pt} | {ct} | - | "
                  f"{r['total_wall_s']:.2f} | {r['error'] or ''} |")
        print()
        print(f"- first-token latency (all): p50={pctl(lat_all, .5):.2f}s p95={pctl(lat_all, .95):.2f}s")
        print(f"- first-token latency (retrieval qs): p50={pctl(lat_tool, .5):.2f}s p95={pctl(lat_tool, .95):.2f}s")
        print(f"- tokens/question: mean={statistics.mean(tok_totals):.0f} "
              f"median={statistics.median(tok_totals):.0f} total={sum(tok_totals)}")
        print(f"- llm calls/question: {statistics.mean(call_counts):.2f} mean")


if __name__ == "__main__":
    main()
