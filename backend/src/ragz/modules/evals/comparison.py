"""Transient single-query versus multi-query answer comparison.

This is an evaluation path, not chat: it persists no messages and carries no
history, agent tools, web search, or general-knowledge fallback. Both variants
use the same workspace/model snapshot and production retrieval/prompting seams;
only the request-scoped multi-query override differs.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.chat.llm import LLMCompleter
from ragz.modules.chat.prompting import (
    PromptSource,
    build_messages,
    count_tokens,
    fit_sources,
    parse_citation_markers,
    split_budget,
)
from ragz.modules.documents import service as documents_service
from ragz.modules.evals.schemas import ComparisonSourceOut, ComparisonVariantOut
from ragz.modules.quotas import service as quota_service
from ragz.modules.retrieval.service import MetadataClause, RetrievalResult
from ragz.modules.tenancy.context import TenantContext
from ragz.modules.tenancy.views import WorkspaceView

_SNIPPET_CHARS = 300
_DECLINE_ANSWER = "The indexed documents did not provide enough evidence to answer this question."


class ComparisonRetriever(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        ctx: TenantContext,
        workspace_id: UUID,
        query: str,
        top_k: int | None = None,
        metadata_clauses: Sequence[MetadataClause] | None = None,
        *,
        multi_query_enabled_override: bool | None = None,
    ) -> RetrievalResult: ...


async def _sources(
    session: AsyncSession,
    ctx: TenantContext,
    result: RetrievalResult,
    *,
    source_budget: int,
    model_name: str,
) -> tuple[list[ComparisonSourceOut], list[PromptSource]]:
    documents: dict[UUID, tuple[str, int]] = {}
    output: list[ComparisonSourceOut] = []
    prompt_sources: list[PromptSource] = []
    for marker, chunk in enumerate(result.chunks, start=1):
        if chunk.document_id not in documents:
            document = await documents_service.get_document_checked(
                session, ctx, chunk.document_id
            )
            documents[chunk.document_id] = (document.filename, document.version)
        filename, version = documents[chunk.document_id]
        output.append(
            ComparisonSourceOut(
                marker=marker,
                document_id=chunk.document_id,
                filename=filename,
                page=chunk.page,
                chunk_index=chunk.chunk_index,
                score=chunk.score,
                snippet=chunk.text[:_SNIPPET_CHARS],
                section=chunk.section,
                version=version,
            )
        )
        prompt_sources.append(
            PromptSource(
                marker=marker,
                filename=filename,
                page=chunk.page,
                text=chunk.text,
                section=chunk.section,
            )
        )
    kept = fit_sources(prompt_sources, source_budget, model_name)
    return output[: len(kept)], kept


async def _compare_one(
    session: AsyncSession,
    ctx: TenantContext,
    workspace: WorkspaceView,
    *,
    question: str,
    model_id: UUID,
    model_name: str,
    completer: LLMCompleter,
    retriever: ComparisonRetriever,
    multi_query: bool,
    token_budget: int,
    usage_operation_id: str,
) -> ComparisonVariantOut:
    total_started = time.perf_counter()
    retrieval_started = time.perf_counter()
    result = await retriever(
        session,
        ctx,
        workspace.id,
        question,
        top_k=workspace.top_k,
        multi_query_enabled_override=multi_query,
    )
    retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
    # Retrieval owns provider accounting, including legacy seams that stage
    # with commit=False. Finalize it before any fallible source authorization.
    await session.commit()

    split = split_budget(token_budget)
    source_budget = max(split.sources - count_tokens(question, model_name), 0)
    sources, prompt_sources = await _sources(
        session,
        ctx,
        result,
        source_budget=source_budget,
        model_name=model_name,
    )
    generation_ms = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    if result.no_answer or not prompt_sources:
        answer = _DECLINE_ANSWER
    else:
        messages = build_messages(
            sources=prompt_sources,
            history=[],
            user_query=question,
            budget=token_budget,
            system_prompt_override=workspace.system_prompt_override,
            model_hint=model_name,
        )
        generation_started = time.perf_counter()
        completion = await completer.complete(model=model_name, messages=messages)
        generation_ms = (time.perf_counter() - generation_started) * 1000
        answer = completion.text
        prompt_tokens = completion.usage.prompt_tokens
        completion_tokens = completion.usage.completion_tokens
        await quota_service.record_usage_durable(
            session,
            org_id=ctx.org_id,
            user_id=ctx.user_id,
            workspace_id=workspace.id,
            model_id=model_id,
            feature="chat",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            idempotency_key=f"comparison:{usage_operation_id}:generation",
        )

    return ComparisonVariantOut(
        mode="multi" if multi_query else "single",
        answer=answer,
        sources=sources,
        citation_markers=parse_citation_markers(answer, len(sources)),
        no_answer=result.no_answer or not prompt_sources,
        query_count=result.query_count,
        retrieval_ms=round(retrieval_ms, 3),
        generation_ms=round(generation_ms, 3),
        total_ms=round((time.perf_counter() - total_started) * 1000, 3),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


async def compare_answers(
    session: AsyncSession,
    ctx: TenantContext,
    workspace: WorkspaceView,
    *,
    question: str,
    model_id: UUID,
    model_name: str,
    completer: LLMCompleter,
    retriever: ComparisonRetriever,
    token_budget: int,
) -> list[ComparisonVariantOut]:
    """Run sequentially on one session; never mutate the workspace toggle."""
    usage_run_id = uuid4().hex
    single = await _compare_one(
        session,
        ctx,
        workspace,
        question=question,
        model_id=model_id,
        model_name=model_name,
        completer=completer,
        retriever=retriever,
        multi_query=False,
        token_budget=token_budget,
        usage_operation_id=f"{usage_run_id}:single",
    )
    multi = await _compare_one(
        session,
        ctx,
        workspace,
        question=question,
        model_id=model_id,
        model_name=model_name,
        completer=completer,
        retriever=retriever,
        multi_query=True,
        token_budget=token_budget,
        usage_operation_id=f"{usage_run_id}:multi",
    )
    return [single, multi]
