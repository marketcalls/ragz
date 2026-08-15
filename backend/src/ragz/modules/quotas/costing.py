"""Estimated-$ cost math for usage-ledger rows (design 2026-08-15 §2/§4).

Pure and price-lookup-agnostic: the reporting layer (Phase 2) resolves the
per-token catalog rates (model_catalog.input_cost_per_token /
output_cost_per_token) and the per-call config rates
(RAGZ_RERANK_USD_PER_CALL / RAGZ_WEB_SEARCH_USD_PER_CALL) and hands them in.
Never touches the DB and never runs on the hot path -- recording
(quotas.service.record_usage) stays independent of pricing, so a missing/zero
price simply reports $0 rather than blocking a write.
"""

# Token features price off model_catalog cost/token; per-call features off a
# flat config rate. Any feature not listed as per-call is treated as token-priced.
TOKEN_FEATURES = frozenset({"chat", "ingestion", "embedding"})
PER_CALL_FEATURES = frozenset({"rerank", "web_search"})


def token_cost(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    input_cost_per_token: float | None,
    output_cost_per_token: float | None,
) -> float:
    """$ for a token feature: prompt×input_rate + completion×output_rate.
    A `None` rate (model not in catalog / price withheld) contributes 0."""
    return prompt_tokens * (input_cost_per_token or 0.0) + completion_tokens * (
        output_cost_per_token or 0.0
    )


def per_call_cost(units: int, *, usd_per_call: float) -> float:
    """$ for a per-call feature: call/search-unit count × the flat rate."""
    return units * usd_per_call


def estimate_cost(
    feature: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    units: int = 0,
    input_cost_per_token: float | None = None,
    output_cost_per_token: float | None = None,
    usd_per_call: float = 0.0,
) -> float:
    """Estimated $ for one ledger row. Per-call features (rerank, web_search)
    price off `units × usd_per_call`; every other feature prices off tokens."""
    if feature in PER_CALL_FEATURES:
        return per_call_cost(units, usd_per_call=usd_per_call)
    return token_cost(
        prompt_tokens, completion_tokens,
        input_cost_per_token=input_cost_per_token,
        output_cost_per_token=output_cost_per_token,
    )
