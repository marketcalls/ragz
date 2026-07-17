# ADR-0002: Phase 1 Hybrid Retrieval — TEI Dense + FastEmbed Sparse

**Date:** 2026-07-18
**Status:** Accepted

## Context

PRD CHAT-2 specifies hybrid retrieval with bge-m3's dual dense + sparse output. TEI serves bge-m3 dense embeddings well but does not emit bge-m3's learned sparse weights; serving those requires a custom FlagEmbedding service. Alternatives considered: (A) TEI dense + FastEmbed BM25-family sparse with Qdrant-native RRF fusion; (B) custom bge-m3 service emitting both vector types; (C) dense-only in Phase 1.

## Decision

Option A. Dense vectors from TEI (bge-m3), sparse vectors from Qdrant's FastEmbed BM25 family computed in the ingestion worker and at query time, fused with RRF via Qdrant's native hybrid query API. The collection schema includes the `sparse` named vector from day one.

## Rationale

- Standard, battle-tested components only — no custom GPU service for self-hosting admins to size and operate (rejects B).
- Hybrid is P0 and keyword-ish queries visibly fail on dense-only; schema-after-data migration risk (rejects C).
- BM25-style sparse is lexically weaker than bge-m3 learned sparse cross-language, but the Phase 3 reranker recovers most of the gap.

## Consequences

- Swapping in true bge-m3 learned sparse later is an isolated change inside `modules/retrieval/` plus a re-embed job (the DOC-10 machinery); no collection migration since the named vector already exists.
- Query-time sparse encoding is CPU-cheap and runs in-process.
