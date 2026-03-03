"""Tests for frontmatter parsing, validation, and date handling."""

from __future__ import annotations

from datetime import date  # noqa: TC003

from hive.frontmatter import (
    Frontmatter,
    extract_body,
    parse_date,
    parse_frontmatter,
    validate_frontmatter,
)

# ── parse_frontmatter ───────────────────────────────────────────────


class TestParseFrontmatter:
    def test_valid_full(self) -> None:
        text = (
            "---\nid: proj\ntype: project\nstatus: active\n"
            'tags: [python, mcp]\nstack: [fastmcp]\ncreated: "2025-06-01"\n---\n\n# Body\n'
        )
        fm = parse_frontmatter(text)
        assert fm is not None
        assert fm.id == "proj"
        assert fm.type == "project"
        assert fm.status == "active"
        assert fm.tags == ["python", "mcp"]
        assert fm.stack == ["fastmcp"]
        assert fm.created == "2025-06-01"

    def test_minimal_fields(self) -> None:
        text = "---\nid: x\ntype: adr\nstatus: done\n---\n\n# ADR\n"
        fm = parse_frontmatter(text)
        assert fm is not None
        assert fm.tags == []
        assert fm.stack == []
        assert fm.created == ""

    def test_no_frontmatter(self) -> None:
        assert parse_frontmatter("# Just markdown\n") is None

    def test_missing_closing_delimiter(self) -> None:
        assert parse_frontmatter("---\nid: x\ntype: y\n") is None

    def test_invalid_yaml(self) -> None:
        assert parse_frontmatter("---\n: broken: yaml: [[\n---\n") is None

    def test_non_dict_yaml(self) -> None:
        assert parse_frontmatter("---\n- list\n- item\n---\n") is None

    def test_missing_optional_fields_default(self) -> None:
        text = "---\nfoo: bar\n---\n\nBody\n"
        fm = parse_frontmatter(text)
        assert fm is not None
        assert fm.id == ""
        assert fm.type == ""
        assert fm.status == ""

    def test_frozen_dataclass(self) -> None:
        text = "---\nid: x\ntype: y\nstatus: z\n---\n"
        fm = parse_frontmatter(text)
        assert fm is not None
        assert isinstance(fm, Frontmatter)

    def test_tags_as_yaml_list(self) -> None:
        text = "---\nid: x\ntype: y\nstatus: z\ntags:\n  - alpha\n  - beta\n---\n"
        fm = parse_frontmatter(text)
        assert fm is not None
        assert fm.tags == ["alpha", "beta"]

    def test_tags_non_list_ignored(self) -> None:
        text = "---\nid: x\ntype: y\nstatus: z\ntags: plain-string\n---\n"
        fm = parse_frontmatter(text)
        assert fm is not None
        assert fm.tags == []


# ── validate_frontmatter ────────────────────────────────────────────


class TestValidateFrontmatter:
    def test_valid(self) -> None:
        content = "---\nid: x\ntype: adr\nstatus: active\n---\n\n# ADR\n"
        assert validate_frontmatter(content) is None

    def test_no_frontmatter(self) -> None:
        err = validate_frontmatter("# No frontmatter\n")
        assert err is not None
        assert "---" in err

    def test_missing_closing(self) -> None:
        err = validate_frontmatter("---\nid: x\ntype: y\n")
        assert err is not None
        assert "closing" in err.lower()

    def test_invalid_yaml(self) -> None:
        err = validate_frontmatter("---\n: broken: [[\n---\n")
        assert err is not None
        assert "yaml" in err.lower()

    def test_non_dict(self) -> None:
        err = validate_frontmatter("---\n- list\n---\n")
        assert err is not None
        assert "mapping" in err.lower()

    def test_missing_required_fields(self) -> None:
        err = validate_frontmatter("---\ntitle: only title\n---\n\n# Body\n")
        assert err is not None
        assert "id" in err
        assert "type" in err
        assert "status" in err


# ── extract_body ────────────────────────────────────────────────────


class TestExtractBody:
    def test_strips_frontmatter(self) -> None:
        text = "---\nid: x\ntype: y\nstatus: z\n---\n\n# Title\nBody text.\n"
        assert extract_body(text) == "# Title\nBody text.\n"

    def test_no_frontmatter_returns_all(self) -> None:
        text = "# Plain markdown\nContent.\n"
        assert extract_body(text) == text

    def test_malformed_returns_all(self) -> None:
        text = "---\nid: x\n"
        assert extract_body(text) == text


# ── parse_date ──────────────────────────────────────────────────────


class TestParseDate:
    def test_iso_date(self) -> None:
        assert parse_date("2025-06-01") == date(2025, 6, 1)

    def test_quoted_date(self) -> None:
        assert parse_date('"2025-06-01"') == date(2025, 6, 1)

    def test_single_quoted(self) -> None:
        assert parse_date("'2025-06-01'") == date(2025, 6, 1)

    def test_empty_string(self) -> None:
        assert parse_date("") is None

    def test_invalid_format(self) -> None:
        assert parse_date("not-a-date") is None

    def test_whitespace_padded(self) -> None:
        assert parse_date("  2025-06-01  ") == date(2025, 6, 1)
