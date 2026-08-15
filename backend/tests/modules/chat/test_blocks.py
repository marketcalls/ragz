"""In-chat generative-UI block schema tests (design doc
docs/superpowers/specs/2026-08-15-in-chat-generative-ui-design.md).

Iron Rule 5: LLM-authored block payloads are hostile input. These tests are
adversarial-heavy: unknown types/fields, oversized payloads, nested-tabs
escape attempts, non-finite numerics, and malformed top-level shapes must
all be dropped/bounded, never raised. Rendering-time HTML/markdown
sanitization is explicitly the FRONTEND's job -- validate_blocks only
bounds and type-checks; a hostile `markdown` string that VALIDATES (because
it's a normal short string) is expected to survive as-is here.
"""

import math

import pytest

from ragz.modules.chat.blocks import (
    _MAX_TAGS,
    MAX_BLOCKS,
    ArticleCardBlock,
    CalloutBlock,
    ChartBlock,
    FormBlock,
    ImageCardBlock,
    InfoCardBlock,
    RankedListBlock,
    SourceRefsBlock,
    TableBlock,
    TabsBlock,
    TagBadgesBlock,
    TextBlock,
    validate_blocks,
)

# --- Valid payloads ---------------------------------------------------------


def test_text_block_valid() -> None:
    out = validate_blocks([{"type": "text", "markdown": "**hello**"}])
    assert len(out) == 1
    assert isinstance(out[0], TextBlock)
    assert out[0].markdown == "**hello**"


def test_chart_block_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "chart",
                "chart": "bar",
                "title": "Revenue",
                "data": [{"month": "Jan", "value": 10.5}, {"month": "Feb", "value": 20}],
                "x_key": "month",
                "keys": ["value"],
            }
        ]
    )
    assert len(out) == 1
    block = out[0]
    assert isinstance(block, ChartBlock)
    assert block.chart == "bar"
    assert block.data[0]["month"] == "Jan"


def test_info_card_block_valid() -> None:
    out = validate_blocks(
        [{"type": "info_card", "title": "Total docs", "body": "42 indexed", "icon": "doc"}]
    )
    assert len(out) == 1
    assert isinstance(out[0], InfoCardBlock)


def test_image_card_block_valid() -> None:
    out = validate_blocks(
        [{"type": "image_card", "title": "Site A", "badge": "New", "image_ref": "doc-img-123"}]
    )
    assert len(out) == 1
    assert isinstance(out[0], ImageCardBlock)
    assert out[0].image_ref == "doc-img-123"


def test_ranked_list_block_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "ranked_list",
                "title": "Top vendors",
                "items": [{"title": "Acme"}, {"title": "Globex", "subtitle": "Runner-up"}],
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], RankedListBlock)
    assert len(out[0].items) == 2


def test_tag_badges_block_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "tag_badges",
                "tags": [{"label": "urgent", "tone": "danger"}, {"label": "info only"}],
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], TagBadgesBlock)
    # default tone applied when omitted
    assert out[0].tags[1].tone == "neutral"


def test_callout_block_valid() -> None:
    out = validate_blocks(
        [{"type": "callout", "tone": "warning", "title": "Heads up", "body": "Check this."}]
    )
    assert len(out) == 1
    assert isinstance(out[0], CalloutBlock)


def test_table_block_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "table",
                "columns": ["Name", "Score"],
                "rows": [["Alice", 9.5], ["Bob", 7]],
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], TableBlock)


def test_tabs_block_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "tabs",
                "tabs": [
                    {
                        "label": "Overview",
                        "blocks": [{"type": "text", "markdown": "hi"}],
                    },
                    {
                        "label": "Details",
                        "blocks": [{"type": "callout", "tone": "info", "body": "detail"}],
                    },
                ],
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], TabsBlock)
    assert len(out[0].tabs) == 2


def test_form_block_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "form",
                "title": "Plan your trip",
                "description": "Tell us a bit more",
                "fields": [
                    {"name": "name", "label": "Your name", "kind": "text", "required": True},
                    {"name": "travellers", "label": "Travellers", "kind": "number"},
                    {
                        "name": "style",
                        "label": "Trip style",
                        "kind": "select",
                        "options": ["Cultural", "Relaxation"],
                    },
                    {
                        "name": "destinations",
                        "label": "Destinations",
                        "kind": "multiselect",
                        "options": ["Tokyo", "Kyoto", "Osaka"],
                    },
                ],
                "submit_label": "Plan it",
            }
        ]
    )
    assert len(out) == 1
    block = out[0]
    assert isinstance(block, FormBlock)
    assert len(block.fields) == 4
    assert block.fields[2].kind == "select"
    assert block.fields[2].options == ["Cultural", "Relaxation"]


def test_form_block_minimal_valid() -> None:
    out = validate_blocks(
        [{"type": "form", "fields": [{"name": "q", "label": "Question", "kind": "text"}]}]
    )
    assert len(out) == 1
    assert isinstance(out[0], FormBlock)
    assert out[0].fields[0].required is False


def test_mixed_list_of_valid_blocks() -> None:
    out = validate_blocks(
        [
            {"type": "text", "markdown": "intro"},
            {"type": "callout", "tone": "success", "body": "done"},
            {"type": "tag_badges", "tags": [{"label": "x"}]},
        ]
    )
    assert len(out) == 3
    assert [type(b) for b in out] == [TextBlock, CalloutBlock, TagBadgesBlock]


# --- Dropped, not raised -----------------------------------------------------


def test_unknown_type_dropped() -> None:
    out = validate_blocks([{"type": "iframe", "src": "evil"}])
    assert out == []


def test_unknown_chart_kind_dropped() -> None:
    out = validate_blocks(
        [{"type": "chart", "chart": "pie3d_explode", "data": [{"a": 1.0}]}]
    )
    assert out == []


def test_extra_field_dropped() -> None:
    out = validate_blocks(
        [{"type": "text", "markdown": "hi", "onclick": "alert(1)"}]
    )
    assert out == []


def test_extra_field_dropped_nested_tag() -> None:
    out = validate_blocks(
        [{"type": "tag_badges", "tags": [{"label": "x", "href": "javascript:alert(1)"}]}]
    )
    assert out == []


def test_missing_required_field_dropped() -> None:
    out = validate_blocks([{"type": "callout", "tone": "info"}])  # body is required
    assert out == []


def test_one_bad_block_does_not_poison_the_rest() -> None:
    out = validate_blocks(
        [
            {"type": "text", "markdown": "ok"},
            {"type": "nonsense"},
            {"type": "callout", "tone": "info", "body": "ok too"},
        ]
    )
    assert len(out) == 2
    assert isinstance(out[0], TextBlock)
    assert isinstance(out[1], CalloutBlock)


# --- Oversized / bounded -----------------------------------------------------


def test_over_max_blocks_truncated() -> None:
    payload = [{"type": "text", "markdown": "x"} for _ in range(MAX_BLOCKS + 50)]
    out = validate_blocks(payload)
    assert len(out) == MAX_BLOCKS


def test_table_with_10000_rows_dropped() -> None:
    huge_rows = [["a", 1.0] for _ in range(10_000)]
    out = validate_blocks([{"type": "table", "columns": ["a", "b"], "rows": huge_rows}])
    assert out == []


def test_table_row_wider_than_columns_dropped() -> None:
    out = validate_blocks(
        [{"type": "table", "columns": ["a"], "rows": [["x"] * 50]}]
    )
    assert out == []


def test_tabs_in_tabs_dropped() -> None:
    """Nesting depth is bounded STATICALLY (InnerBlock excludes TabsBlock),
    so a tabs-inside-tabs payload fails validation of the outer tab's
    `blocks` list -- the whole tabs block is dropped, never partially
    rendered with runaway nesting."""
    out = validate_blocks(
        [
            {
                "type": "tabs",
                "tabs": [
                    {
                        "label": "outer",
                        "blocks": [
                            {
                                "type": "tabs",
                                "tabs": [{"label": "inner", "blocks": []}],
                            }
                        ],
                    }
                ],
            }
        ]
    )
    assert out == []


def test_1000_tags_dropped() -> None:
    out = validate_blocks(
        [{"type": "tag_badges", "tags": [{"label": "x"} for _ in range(1000)]}]
    )
    assert out == []


def test_1000_ranked_list_items_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "ranked_list",
                "items": [{"title": "x"} for _ in range(1000)],
            }
        ]
    )
    assert out == []


def test_too_many_tabs_dropped() -> None:
    out = validate_blocks(
        [{"type": "tabs", "tabs": [{"label": "t", "blocks": []} for _ in range(50)]}]
    )
    assert out == []


def test_too_many_blocks_in_one_tab_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "tabs",
                "tabs": [
                    {
                        "label": "t",
                        "blocks": [{"type": "text", "markdown": "x"} for _ in range(50)],
                    }
                ],
            }
        ]
    )
    assert out == []


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_nan_inf_chart_value_dropped(bad_value: float) -> None:
    out = validate_blocks([{"type": "chart", "chart": "line", "data": [{"x": bad_value}]}])
    assert out == []


def test_nan_inf_table_cell_dropped() -> None:
    out = validate_blocks(
        [{"type": "table", "columns": ["a"], "rows": [[math.nan]]}]
    )
    assert out == []


def test_giant_markdown_string_dropped() -> None:
    giant = "a" * (1024 * 1024)  # 1MB
    out = validate_blocks([{"type": "text", "markdown": giant}])
    assert out == []


def test_short_markdown_under_limit_kept() -> None:
    out = validate_blocks([{"type": "text", "markdown": "a" * 8000}])
    assert len(out) == 1


# --- Hostile content that VALIDATES (frontend sanitizes on render) ---------


def test_hostile_markdown_validates_and_is_kept_verbatim() -> None:
    """Iron Rule 5: this module does NOT sanitize/execute anything -- it only
    bounds and type-checks. An XSS payload embedded in `markdown` is a
    syntactically valid short string, so it VALIDATES and is kept as-is;
    neutralizing it is the FRONTEND's job (sanitized-markdown renderer +
    BlockRenderer type whitelist), not this validator's."""
    payload = "<img src=x onerror=alert(1)>"
    out = validate_blocks([{"type": "text", "markdown": payload}])
    assert len(out) == 1
    assert isinstance(out[0], TextBlock)
    assert out[0].markdown == payload


@pytest.mark.parametrize(
    "image_ref",
    ["javascript:alert(1)", "http://evil.example.com/x.png", "data:text/html,<script>1</script>"],
)
def test_image_ref_accepted_as_opaque_bounded_string(image_ref: str) -> None:
    """image_ref is validated here ONLY as a short opaque string id -- this
    module never interprets it as a URL. A `javascript:`/external/`data:`
    value is accepted at this layer because nothing here loads it; the
    renderer (a later task) resolves image_ref server-side to a proxied,
    ACL-checked URL and must never treat this field as a directly-loadable
    URL."""
    out = validate_blocks([{"type": "image_card", "title": "x", "image_ref": image_ref}])
    assert len(out) == 1
    assert isinstance(out[0], ImageCardBlock)
    assert out[0].image_ref == image_ref


def test_image_ref_over_length_dropped() -> None:
    out = validate_blocks([{"type": "image_card", "title": "x", "image_ref": "y" * 5000}])
    assert out == []


# --- FormBlock: dropped/bounded, hostile-but-inert -------------------------


def test_form_select_without_options_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "form",
                "fields": [{"name": "style", "label": "Style", "kind": "select"}],
            }
        ]
    )
    assert out == []


def test_form_multiselect_with_empty_options_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "form",
                "fields": [
                    {
                        "name": "dest",
                        "label": "Destinations",
                        "kind": "multiselect",
                        "options": [],
                    }
                ],
            }
        ]
    )
    assert out == []


def test_form_too_many_fields_dropped() -> None:
    fields = [{"name": f"f{i}", "label": "Field", "kind": "text"} for i in range(11)]
    out = validate_blocks([{"type": "form", "fields": fields}])
    assert out == []


def test_form_zero_fields_dropped() -> None:
    out = validate_blocks([{"type": "form", "fields": []}])
    assert out == []


def test_form_too_many_options_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "form",
                "fields": [
                    {
                        "name": "s",
                        "label": "Select",
                        "kind": "select",
                        "options": [f"opt{i}" for i in range(21)],
                    }
                ],
            }
        ]
    )
    assert out == []


def test_form_oversize_label_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "form",
                "fields": [{"name": "q", "label": "x" * 121, "kind": "text"}],
            }
        ]
    )
    assert out == []


def test_form_oversize_option_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "form",
                "fields": [
                    {
                        "name": "s",
                        "label": "Select",
                        "kind": "select",
                        "options": ["y" * 121],
                    }
                ],
            }
        ]
    )
    assert out == []


def test_form_unknown_field_kind_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "form",
                "fields": [{"name": "q", "label": "Question", "kind": "checkbox"}],
            }
        ]
    )
    assert out == []


def test_form_extra_field_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "form",
                "fields": [{"name": "q", "label": "Question", "kind": "text"}],
                "onsubmit": "evil()",
            }
        ]
    )
    assert out == []


def test_form_hostile_label_validates_and_kept_verbatim() -> None:
    """Same contract as hostile markdown: a `<script>`/`javascript:` label is
    a syntactically valid bounded string, so it VALIDATES and is kept as-is;
    the frontend renders it as inert text, never executes it."""
    out = validate_blocks(
        [
            {
                "type": "form",
                "fields": [
                    {
                        "name": "q",
                        "label": "<script>alert(1)</script>",
                        "kind": "text",
                        "placeholder": "javascript:alert(1)",
                    }
                ],
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], FormBlock)
    assert out[0].fields[0].label == "<script>alert(1)</script>"
    assert out[0].fields[0].placeholder == "javascript:alert(1)"


def test_form_nested_inside_tab_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "tabs",
                "tabs": [
                    {
                        "label": "Details",
                        "blocks": [
                            {
                                "type": "form",
                                "fields": [
                                    {"name": "q", "label": "Question", "kind": "text"}
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], TabsBlock)
    assert isinstance(out[0].tabs[0].blocks[0], FormBlock)


def test_tabs_inside_form_field_impossible_shape_dropped() -> None:
    """FormField has no nested-block field at all, so a payload trying to
    smuggle a tabs block into a form field is just an unknown/extra field
    and the whole form is dropped -- there is no code path for it to nest."""
    out = validate_blocks(
        [
            {
                "type": "form",
                "fields": [
                    {
                        "name": "q",
                        "label": "Question",
                        "kind": "text",
                        "blocks": [{"type": "tabs", "tabs": []}],
                    }
                ],
            }
        ]
    )
    assert out == []


@pytest.mark.parametrize(
    "raw",
    [
        [{"type": "form"}],
        [{"type": "form", "fields": "not-a-list"}],
        [{"type": "form", "fields": None}],
        [{"type": "form", "fields": [None]}],
        [{"type": "form", "fields": [{"name": "q", "label": "Q", "kind": "text"}] * 1000}],
        [
            {
                "type": "form",
                "fields": [{"name": "q", "label": "Q", "kind": "select", "options": None}],
            }
        ],
        [
            {
                "type": "form",
                "fields": [{"name": "q", "label": "Q", "kind": "select", "options": "not-a-list"}],
            }
        ],
        [{"type": "form", "fields": [{"kind": "text"}]}],  # missing name/label
    ],
)
def test_form_shaped_hostile_input_never_raises(raw: object) -> None:
    result = validate_blocks(raw)
    assert isinstance(result, list)


# --- Malformed top-level shapes: never raise, always [] --------------------


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "text", "markdown": "not a list"},
        None,
        "just a string",
        12345,
        12.5,
        True,
        b"bytes",
        (),
        {"blocks": []},
    ],
)
def test_non_list_top_level_returns_empty(raw: object) -> None:
    assert validate_blocks(raw) == []


# --- SourceRefsBlock + card links (openui-parity T1) -----------------------


def test_source_refs_block_valid_web_and_doc_items() -> None:
    out = validate_blocks(
        [
            {
                "type": "source_refs",
                "title": "Sources",
                "items": [
                    {
                        "title": "Example Article",
                        "source": "example.com",
                        "url": "https://example.com/article",
                    },
                    {
                        "title": "Policy Doc",
                        "document_id": "doc-123",
                        "page": 4,
                    },
                ],
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], SourceRefsBlock)
    assert len(out[0].items) == 2
    assert out[0].items[0].url == "https://example.com/article"
    assert out[0].items[1].document_id == "doc-123"
    assert out[0].items[1].page == 4


def test_source_refs_item_extra_field_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "source_refs",
                "items": [
                    {"title": "x", "url": "https://example.com", "onclick": "evil()"}
                ],
            }
        ]
    )
    assert out == []


def test_source_ref_both_url_and_document_id_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "source_refs",
                "items": [
                    {"title": "x", "url": "https://example.com", "document_id": "doc-1"}
                ],
            }
        ]
    )
    assert out == []


def test_source_ref_neither_url_nor_document_id_dropped() -> None:
    out = validate_blocks([{"type": "source_refs", "items": [{"title": "x"}]}])
    assert out == []


def test_source_ref_page_without_document_id_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "source_refs",
                "items": [{"title": "x", "url": "https://example.com", "page": 5}],
            }
        ]
    )
    assert out == []


def test_source_ref_javascript_url_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "source_refs",
                "items": [{"title": "x", "url": "javascript:alert(1)"}],
            }
        ]
    )
    assert out == []


def test_source_refs_too_many_items_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "source_refs",
                "items": [{"title": "x", "url": "https://example.com"} for _ in range(25)],
            }
        ]
    )
    assert out == []


def test_info_card_valid_url() -> None:
    out = validate_blocks(
        [{"type": "info_card", "title": "x", "url": "https://example.com/doc"}]
    )
    assert len(out) == 1
    assert isinstance(out[0], InfoCardBlock)
    assert out[0].url == "https://example.com/doc"


def test_info_card_javascript_url_dropped() -> None:
    out = validate_blocks(
        [{"type": "info_card", "title": "x", "url": "javascript:alert(1)"}]
    )
    assert out == []


def test_ranked_list_item_valid_url_and_image_ref() -> None:
    out = validate_blocks(
        [
            {
                "type": "ranked_list",
                "items": [
                    {
                        "title": "Acme",
                        "url": "https://acme.example.com",
                        "image_ref": "img-1",
                    }
                ],
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], RankedListBlock)
    assert out[0].items[0].url == "https://acme.example.com"
    assert out[0].items[0].image_ref == "img-1"


# --- ArticleCardBlock: standard + hero (openui-parity T2) ------------------


def test_article_card_standard_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "article_card",
                "title": "Q3 Roadmap",
                "tags": [{"label": "product"}, {"label": "urgent", "tone": "danger"}],
                "url": "https://example.com/roadmap",
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], ArticleCardBlock)
    assert out[0].layout == "standard"
    assert out[0].url == "https://example.com/roadmap"
    assert len(out[0].tags or []) == 2


def test_article_card_hero_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "article_card",
                "title": "Big Launch",
                "layout": "hero",
                "badge": "Featured",
                "image_ref": "hero-img-1",
            }
        ]
    )
    assert len(out) == 1
    block = out[0]
    assert isinstance(block, ArticleCardBlock)
    assert block.layout == "hero"
    assert block.badge == "Featured"
    assert block.image_ref == "hero-img-1"
    assert block.url is None
    assert block.document_id is None


def test_article_card_document_id_and_page_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "article_card",
                "title": "Policy Doc",
                "document_id": "doc-123",
                "page": 4,
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], ArticleCardBlock)
    assert out[0].document_id == "doc-123"
    assert out[0].page == 4
    assert out[0].url is None


def test_article_card_both_url_and_document_id_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "article_card",
                "title": "x",
                "url": "https://example.com",
                "document_id": "doc-1",
            }
        ]
    )
    assert out == []


def test_article_card_page_without_document_id_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "article_card",
                "title": "x",
                "url": "https://example.com",
                "page": 5,
            }
        ]
    )
    assert out == []


def test_article_card_javascript_url_dropped() -> None:
    out = validate_blocks(
        [{"type": "article_card", "title": "x", "url": "javascript:alert(1)"}]
    )
    assert out == []


def test_article_card_too_many_tags_dropped() -> None:
    out = validate_blocks(
        [
            {
                "type": "article_card",
                "title": "x",
                "tags": [{"label": "t"} for _ in range(_MAX_TAGS + 1)],
            }
        ]
    )
    assert out == []


def test_article_card_extra_field_dropped() -> None:
    out = validate_blocks(
        [{"type": "article_card", "title": "x", "onclick": "evil()"}]
    )
    assert out == []


def test_article_card_nested_inside_tab_valid() -> None:
    out = validate_blocks(
        [
            {
                "type": "tabs",
                "tabs": [
                    {
                        "label": "Articles",
                        "blocks": [
                            {
                                "type": "article_card",
                                "title": "Nested Article",
                                "url": "https://example.com/nested",
                            }
                        ],
                    }
                ],
            }
        ]
    )
    assert len(out) == 1
    assert isinstance(out[0], TabsBlock)
    assert isinstance(out[0].tabs[0].blocks[0], ArticleCardBlock)


# --- Malformed top-level shapes: never raise, always [] --------------------


@pytest.mark.parametrize(
    "raw",
    [
        [],
        [None],
        ["a string block"],
        [123],
        [[]],
        [True, False],
        [{}],
        [{"no_type_field": True}],
        [{"type": None}],
        [{"type": 123}],
        [{"type": "text"}],  # missing required markdown
        [{"type": "text", "markdown": None}],
        [{"type": "text", "markdown": 123}],
        [{"type": "chart", "chart": "bar", "data": "not-a-list"}],
        [{"type": "chart", "chart": "bar", "data": [{"a": "fine"}, {"b": [1, 2, 3]}]}],
        [{"type": "table", "columns": "not-a-list", "rows": []}],
        [{"type": "tabs", "tabs": "not-a-list"}],
        [{}, {"type": "text", "markdown": "ok"}, None, 42, {"type": "unknown"}],
        [{"type": "text", "markdown": "ok"}] * 1000,
        {"type": "text"},
        [{"type": "text", "markdown": "ok", "nested": {"a": {"b": {"c": "d" * 100000}}}}],
    ],
)
def test_validate_blocks_never_raises(raw: object) -> None:
    """Property-style: validate_blocks must never raise for any malformed
    shape thrown at it -- it always returns a (possibly empty) list."""
    result = validate_blocks(raw)
    assert isinstance(result, list)
