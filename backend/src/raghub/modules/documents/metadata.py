"""Per-workspace metadata schema: admin-defined fields + document values (DOC-6).

Fields are the schema (text|date|select); values live on Document.meta (JSONB)
and are mirrored into the Qdrant payload under a nested `meta` object via
retrieval.service.update_document_metadata — never post-filtered, always
write-through so retrieval's metadata_clauses (Task 10) can filter on them.
"""

import re
from collections.abc import Mapping
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from raghub.core.errors import ConflictError, NotFoundError
from raghub.modules.audit.service import record_audit
from raghub.modules.documents.models import Document, MetadataField
from raghub.modules.documents.service import get_document_checked
from raghub.modules.retrieval import service as retrieval_service
from raghub.modules.retrieval.service import MetadataClause
from raghub.modules.tenancy.context import TenantContext
from raghub.modules.tenancy.models import Workspace
from raghub.modules.tenancy.service import get_workspace_checked

_NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_FIELD_TYPES = ("text", "date", "select")

# The addendum's revision-date/department/doc-type row (2026-07-19 addendum).
PRESET_FIELDS: tuple[dict[str, object], ...] = (
    {"name": "department", "label": "Department", "field_type": "text", "options": None},
    {
        "name": "doc_type", "label": "Document Type", "field_type": "select",
        "options": ["policy", "procedure", "manual", "report", "drawing", "other"],
    },
    {"name": "revision_date", "label": "Revision Date", "field_type": "date", "options": None},
)


async def list_fields(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID
) -> list[MetadataField]:
    """All metadata fields for a workspace, position-ordered.

    Lazy idempotent preset seed (judgment call, Plan H): the FIRST call on a
    workspace with zero fields seeds PRESET_FIELDS, so pre-H workspaces and
    brand-new ones alike get department/doc_type/revision_date without a data
    migration touching every workspace row. Keeps the tenancy -> documents
    import direction clean (no workspace-creation hook needed in tenancy).
    Idempotent: a workspace that already has ANY field (preset or custom)
    is never reseeded.
    """
    ws = await get_workspace_checked(session, ctx, workspace_id)
    existing = list(
        (
            await session.execute(
                select(MetadataField)
                .where(MetadataField.workspace_id == ws.id)
                .order_by(MetadataField.position)
            )
        ).scalars()
    )
    if existing:
        return existing
    for position, preset in enumerate(PRESET_FIELDS):
        session.add(MetadataField(workspace_id=ws.id, position=position, **preset))
    await session.commit()
    return list(
        (
            await session.execute(
                select(MetadataField)
                .where(MetadataField.workspace_id == ws.id)
                .order_by(MetadataField.position)
            )
        ).scalars()
    )


async def create_field(
    session: AsyncSession,
    ctx: TenantContext,
    workspace_id: UUID,
    *,
    name: str,
    label: str,
    field_type: str,
    options: list[str] | None,
) -> MetadataField:
    """Admin-only field creation. Validates name/type/options, rejects a
    duplicate name (409), registers the Qdrant payload index for the field
    (so it's filterable the moment a document sets a value), and audits."""
    ws = await get_workspace_checked(session, ctx, workspace_id)
    if not _NAME_RE.match(name):
        raise ConflictError(f"'{name}' is not a valid field name (^[a-z0-9_]{{1,40}}$)")
    if field_type not in _FIELD_TYPES:
        raise ConflictError(f"field_type must be one of {_FIELD_TYPES}")
    if field_type == "select" and not options:
        raise ConflictError("select fields require at least one option")
    dup = (
        await session.execute(
            select(MetadataField).where(
                MetadataField.workspace_id == ws.id, MetadataField.name == name
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise ConflictError(f"field '{name}' already exists in this workspace")
    position = (
        await session.execute(
            select(func.count()).select_from(MetadataField).where(
                MetadataField.workspace_id == ws.id
            )
        )
    ).scalar_one()
    field = MetadataField(
        workspace_id=ws.id, name=name, label=label, field_type=field_type,
        options=list(options) if options else None, position=position,
    )
    session.add(field)
    await session.flush()
    collection_name = await retrieval_service.resolve_collection_name(session, ws.id)
    await retrieval_service.ensure_metadata_index(name, field_type, collection_name=collection_name)
    await record_audit(
        session, org_id=ctx.org_id, actor_id=ctx.user_id,
        action="metadata_field.created", target_type="metadata_field", target_id=str(field.id),
    )
    await session.commit()
    return field


async def delete_field(session: AsyncSession, ctx: TenantContext, field_id: UUID) -> None:
    """Admin-only field deletion, org-scoped via a workspace join (the route
    carries no workspace_id — DELETE /metadata-fields/{field_id}). Existing
    document values under this field's name are left in place on Document.meta
    and in the Qdrant payload: they simply become unfilterable (no field
    definition left to validate or index them against). Documented, not a bug."""
    field = (
        await session.execute(
            select(MetadataField)
            .join(Workspace, Workspace.id == MetadataField.workspace_id)
            .where(MetadataField.id == field_id, Workspace.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if field is None:
        raise NotFoundError("metadata field not found")
    await session.delete(field)
    await record_audit(
        session, org_id=ctx.org_id, actor_id=ctx.user_id,
        action="metadata_field.deleted", target_type="metadata_field", target_id=str(field_id),
    )
    await session.commit()


def _validate_value(field: MetadataField, raw: str) -> None:
    if field.field_type == "select":
        if raw not in (field.options or []):
            raise ConflictError(f"'{raw}' is not an option of {field.name}")
    elif field.field_type == "date":
        try:
            date.fromisoformat(raw)
        except ValueError as exc:
            raise ConflictError(f"{field.name} must be an ISO date (YYYY-MM-DD)") from exc


async def set_document_metadata(
    session: AsyncSession, ctx: TenantContext, document_id: UUID, values: dict[str, str]
) -> Document:
    """Full replacement of a document's metadata values (PUT semantics).
    unknown key -> NotFoundError; select value outside its options / an
    unparsable ISO date -> ConflictError. Commit-then-mirror (F's
    update_document_acl pattern): PG commits first so a later Qdrant failure
    never strands a half-applied value in PG; the caller retries (set_payload
    is idempotent) and the route surfaces the failure as an upstream error."""
    doc = await get_document_checked(session, ctx, document_id)
    fields = {
        f.name: f
        for f in (
            await session.execute(
                select(MetadataField).where(MetadataField.workspace_id == doc.workspace_id)
            )
        ).scalars()
    }
    for key, raw in values.items():
        field = fields.get(key)
        if field is None:
            raise NotFoundError(f"unknown metadata field: {key}")
        _validate_value(field, raw)
    doc.meta = dict(values)
    await record_audit(
        session, org_id=ctx.org_id, actor_id=ctx.user_id,
        action="document.metadata_changed", target_type="document", target_id=str(doc.id),
    )
    await session.commit()
    collection_name = await retrieval_service.resolve_collection_name(session, doc.workspace_id)
    await retrieval_service.update_document_metadata(
        ctx.org_id, doc.id, doc.meta, collection_name=collection_name
    )
    return doc


def _date_bounds(raw: str) -> tuple[str | None, str | None]:
    """Parse the date filter's raw string into (from, to) ISO date strings.

    `"YYYY-MM-DD..YYYY-MM-DD"` splits on the literal `..`; either side may be
    empty for an open-ended range (`"..2026-06-30"`, `"2026-01-01.."`). A bare
    single day (no `..`) is both bounds — the whole day. Malformed dates raise
    ConflictError (409) rather than surviving to blow up inside the filter
    builder as a 500 — this is user-typed filter input."""
    if ".." in raw:
        from_s, _, to_s = raw.partition("..")
        bounds = (from_s or None, to_s or None)
    else:
        bounds = (raw, raw)
    for side in bounds:
        if side is not None:
            try:
                date.fromisoformat(side)
            except ValueError:
                raise ConflictError(f"'{raw}' is not a valid date filter") from None
    return bounds


async def build_clauses(
    session: AsyncSession, ctx: TenantContext, workspace_id: UUID, metadata: Mapping[str, str]
) -> list[MetadataClause]:
    """Turn a caller-supplied `{field_name: raw_value}` mapping into
    retrieval's MetadataClause objects (DOC-6/Task 10) — the ONLY function
    that builds them. Unknown field name -> NotFoundError (never silently
    ignored: a typo must not silently widen the search). The `meta.` prefix
    is applied unconditionally here, regardless of the field's own name, so
    user-supplied filter input can never address a bare Qdrant payload key
    (tenant_id/workspace_id/acl_groups/is_current) — those live outside
    `meta.*` and this function only ever emits `meta.`-prefixed keys."""
    fields = {f.name: f for f in await list_fields(session, ctx, workspace_id)}
    clauses: list[MetadataClause] = []
    for name, raw in metadata.items():
        field = fields.get(name)
        if field is None:
            raise NotFoundError(f"unknown metadata field: {name}")
        key = f"meta.{field.name}"
        if field.field_type == "date":
            from_date, to_date = _date_bounds(raw)
            clauses.append(
                MetadataClause(
                    key=key, kind="date_range",
                    gte=f"{from_date}T00:00:00Z" if from_date else None,
                    lte=f"{to_date}T23:59:59Z" if to_date else None,
                )
            )
        else:
            clauses.append(MetadataClause(key=key, kind="eq", value=raw))
    return clauses
