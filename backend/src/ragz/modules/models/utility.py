"""Utility-model resolver (Phase 3 design D5/§4). The ONE seam every
utility-model-powered feature calls to find the superadmin-designated model:
Auditor/Gatekeeper/escalation-tiebreak (this plan), ingestion enrichment and
rolling-summary memory (Plan K), eval-judged faithfulness (this plan's
modules/evals/). Never query Model.is_utility directly outside this file —
mirrors the retrieve()/resolve_model single-seam convention elsewhere.
"""

from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.models.models import Model


async def get_utility_model(session: AsyncSession) -> Model | None:
    """The designated utility model, or None when no superadmin has set one
    OR the designated row is currently disabled (a disabled utility model
    behaves as if none were designated — mirrors resolve_model's enabled
    gate). Every caller must have a documented no-utility-model fallback:
    Auditor/Gatekeeper skip silently, the escalation tiebreak falls back to
    heuristics-only, eval faithfulness scoring is simply unavailable.

    The `modality == "chat"` guard is defense-in-depth: every utility feature
    issues chat COMPLETIONS, so an embedding model designated as the utility
    model (e.g. is_utility toggled on the wrong row) would make the provider
    reject the request ("not allowed to sample from this model") and surface
    as a generic gateway failure on every chat. Treat such a mis-designation
    as "no utility model" rather than routing chat completions to it."""
    return (
        await session.execute(
            select(Model).where(
                Model.is_utility == true(),
                Model.enabled == true(),
                Model.modality == "chat",
            )
        )
    ).scalar_one_or_none()
