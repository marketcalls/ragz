"""In-chat generative-UI block schema (design doc
docs/superpowers/specs/2026-08-15-in-chat-generative-ui-design.md, "1. Block
schema").

Iron Rule 5 boundary: block payloads are emitted by an LLM and MUST be
treated as hostile. Every block model is `extra="forbid"` and every
list/string field is length-bounded so a model cannot emit a payload large
enough to exhaust the client. `validate_blocks()` is the single entrypoint
that turns arbitrary/adversarial JSON-ish data into a bounded list of valid
blocks -- it NEVER raises to its caller and NEVER logs raw block content
(only a redacted dropped-count).

No sanitization or execution happens here. Text fields (`markdown`, `body`,
titles, ...) are stored as-is, only length-bounded; rendering-time HTML/XSS
sanitization is the FRONTEND's job (the existing sanitized-markdown
renderer + a whitelist `BlockRenderer` switch on `block.type`, per the
design doc). `image_ref` is an opaque, bounded string id here only -- it is
NOT a URL and nothing in this module resolves or loads it; resolving it to
a safe, ACL-checked, server-proxied URL (never an arbitrary external URL)
is a later task.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

# --- Global caps --------------------------------------------------------

MAX_BLOCKS = 24

_MAX_TITLE = 200
_MAX_SUBTITLE = 300
_MAX_BODY = 4000
_MAX_MARKDOWN = 8000
_MAX_BADGE = 60
_MAX_IMAGE_REF = 300

_MAX_CHART_ROWS = 200
_MAX_CHART_KEYS = 20

_MAX_LIST_ITEMS = 20

_MAX_TAGS = 20
_MAX_TAG_LABEL = 60

_MAX_TABLE_COLUMNS = 12
_MAX_TABLE_ROWS = 100
_MAX_TABLE_COL_LEN = 100
_MAX_TABLE_CELL_LEN = 500

_MAX_TABS = 6
_MAX_BLOCKS_PER_TAB = 12

# Whitelisted icon names for InfoCardBlock -- an unrecognized value is a
# validation failure (block dropped), never rendered as arbitrary text/attr.
IconName = Literal[
    "info", "chart", "dollar", "trophy", "warning", "doc", "spark",
    "users", "clock", "check", "star", "target", "globe", "shield", "calendar",
]

ChartKind = Literal[
    "bar", "line", "area", "stacked_area", "donut", "radar", "radial_gauge", "grouped_bar",
]

TagTone = Literal["neutral", "info", "success", "warning", "danger"]
CalloutTone = Literal["info", "success", "warning", "danger"]

_ChartValue = str | float


# Parity with table cells (security review): bound chart-row string values and
# the number of keys per row so a model can't emit huge chart payloads that the
# per-row/keys/count caps above don't otherwise catch.
_MAX_CHART_CELL_LEN = 500
_MAX_CHART_ROW_KEYS = 30


def _row_is_finite(row: dict[str, _ChartValue]) -> bool:
    if len(row) > _MAX_CHART_ROW_KEYS:
        return False
    for key, value in row.items():
        if len(key) > _MAX_CHART_CELL_LEN:
            return False
        if isinstance(value, bool):
            # bool is a float-compatible subtype in Python but never a
            # legitimate chart value; treat it as invalid rather than 0/1.
            return False
        if isinstance(value, str) and len(value) > _MAX_CHART_CELL_LEN:
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
    return True


class TextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    markdown: str = Field(max_length=_MAX_MARKDOWN)


class ChartBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["chart"]
    chart: ChartKind
    title: str | None = Field(default=None, max_length=_MAX_TITLE)
    subtitle: str | None = Field(default=None, max_length=_MAX_SUBTITLE)
    data: list[dict[str, _ChartValue]] = Field(max_length=_MAX_CHART_ROWS)
    x_key: str | None = Field(default=None, max_length=100)
    category_key: str | None = Field(default=None, max_length=100)
    keys: list[str] | None = Field(default=None, max_length=_MAX_CHART_KEYS)

    @field_validator("data")
    @classmethod
    def _reject_non_finite_values(
        cls, rows: list[dict[str, _ChartValue]]
    ) -> list[dict[str, _ChartValue]]:
        for row in rows:
            if not _row_is_finite(row):
                raise ValueError("chart data row contains a non-finite numeric value")
        return rows


class InfoCardBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["info_card"]
    title: str = Field(max_length=_MAX_TITLE)
    subtitle: str | None = Field(default=None, max_length=_MAX_SUBTITLE)
    body: str | None = Field(default=None, max_length=_MAX_BODY)
    icon: IconName | None = None


class ImageCardBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image_card"]
    title: str = Field(max_length=_MAX_TITLE)
    subtitle: str | None = Field(default=None, max_length=_MAX_SUBTITLE)
    badge: str | None = Field(default=None, max_length=_MAX_BADGE)
    # Opaque internal id ONLY -- never treated as a URL in this module.
    # Server-side resolution to a proxied, ACL-checked URL (or dropping the
    # image entirely on an unknown id) is a later task.
    image_ref: str | None = Field(default=None, max_length=_MAX_IMAGE_REF)


class RankedListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=_MAX_TITLE)
    subtitle: str | None = Field(default=None, max_length=_MAX_SUBTITLE)


class RankedListBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ranked_list"]
    title: str | None = Field(default=None, max_length=_MAX_TITLE)
    items: list[RankedListItem] = Field(max_length=_MAX_LIST_ITEMS)


class TagBadge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(max_length=_MAX_TAG_LABEL)
    tone: TagTone = "neutral"


class TagBadgesBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tag_badges"]
    tags: list[TagBadge] = Field(max_length=_MAX_TAGS)


class CalloutBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["callout"]
    tone: CalloutTone
    title: str | None = Field(default=None, max_length=_MAX_TITLE)
    body: str = Field(max_length=_MAX_BODY)


class TableBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["table"]
    columns: list[str] = Field(max_length=_MAX_TABLE_COLUMNS)
    rows: list[list[str | float]] = Field(max_length=_MAX_TABLE_ROWS)

    @field_validator("columns")
    @classmethod
    def _bound_column_length(cls, columns: list[str]) -> list[str]:
        for col in columns:
            if len(col) > _MAX_TABLE_COL_LEN:
                raise ValueError("table column name too long")
        return columns

    @field_validator("rows")
    @classmethod
    def _bound_row_width_and_cells(cls, rows: list[list[str | float]]) -> list[list[str | float]]:
        for row in rows:
            if len(row) > _MAX_TABLE_COLUMNS:
                raise ValueError("table row wider than the maximum column count")
            for cell in row:
                if isinstance(cell, bool):
                    raise ValueError("table cell must not be a bool")
                if isinstance(cell, float) and not math.isfinite(cell):
                    raise ValueError("table cell contains a non-finite numeric value")
                if isinstance(cell, str) and len(cell) > _MAX_TABLE_CELL_LEN:
                    raise ValueError("table cell string too long")
        return rows


# --- Tabs: depth is bounded STATICALLY, not by a runtime counter ----------
#
# `InnerBlock` is the block union usable *inside* a tab. It deliberately
# excludes TabsBlock, so a tab's `blocks` list can never itself contain a
# tabs block -- nesting depth is capped at exactly 1 by the type definition
# itself, not by a depth check that a future edit could forget to enforce.

InnerBlock = Annotated[
    TextBlock
    | ChartBlock
    | InfoCardBlock
    | ImageCardBlock
    | RankedListBlock
    | TagBadgesBlock
    | CalloutBlock
    | TableBlock,
    Field(discriminator="type"),
]


class TabItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(max_length=_MAX_TITLE)
    blocks: list[InnerBlock] = Field(max_length=_MAX_BLOCKS_PER_TAB)


class TabsBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tabs"]
    tabs: list[TabItem] = Field(max_length=_MAX_TABS)


# --- Top-level union -------------------------------------------------------

Block = Annotated[
    TextBlock
    | ChartBlock
    | InfoCardBlock
    | ImageCardBlock
    | RankedListBlock
    | TagBadgesBlock
    | CalloutBlock
    | TableBlock
    | TabsBlock,
    Field(discriminator="type"),
]

_BLOCK_ADAPTER: TypeAdapter[Block] = TypeAdapter(Block)


def validate_blocks(raw: object) -> list[Block]:
    """Iron Rule 5 boundary: `raw` is arbitrary/hostile JSON-ish data (from
    an LLM's "emit blocks" step) and is validated defensively.

    - Not a list -> [] (no partial-credit parsing of a dict/str/number/None).
    - At most MAX_BLOCKS items are attempted; anything beyond the cap is
      dropped without being validated.
    - Each item is validated against the `Block` discriminated union; a
      `ValidationError` (unknown type, extra field, out-of-bounds value,
      wrong shape, ...) drops just that item and processing continues.
    - NEVER raises to the caller, for any input shape.
    - Logs only a redacted dropped-count via structlog -- never raw block
      content (which is untrusted, model-authored text).
    """
    if not isinstance(raw, list):
        return []

    validated: list[Block] = []
    dropped = 0
    for item in raw[:MAX_BLOCKS]:
        try:
            validated.append(_BLOCK_ADAPTER.validate_python(item))
        except ValidationError:
            dropped += 1
        except Exception:  # Iron Rule 5: this boundary must never raise, for any input
            dropped += 1

    overflow = max(0, len(raw) - MAX_BLOCKS)
    dropped += overflow

    if dropped:
        structlog.get_logger().info(
            "generative_ui_blocks_dropped",
            dropped_count=dropped,
            received_count=len(raw),
            kept_count=len(validated),
        )

    return validated
