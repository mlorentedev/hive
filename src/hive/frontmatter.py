"""Frontmatter parsing and validation — single source of truth for YAML metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import yaml

_REQUIRED_FIELDS = frozenset({"id", "type", "status"})

_TERMINAL_STATUSES = frozenset({"completed", "done", "accepted", "deprecated", "archived"})


@dataclass(frozen=True)
class Frontmatter:
    """Parsed YAML frontmatter from a vault markdown file."""

    id: str
    type: str
    status: str
    tags: list[str]
    stack: list[str]
    created: str
    raw: dict[str, object]


def parse_frontmatter(text: str) -> Frontmatter | None:
    """Tolerant frontmatter parser. Returns None if no valid frontmatter found."""
    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None

    if not isinstance(fm, dict):
        return None

    def _as_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    return Frontmatter(
        id=str(fm.get("id", "")),
        type=str(fm.get("type", "")),
        status=str(fm.get("status", "")),
        tags=_as_list(fm.get("tags")),
        stack=_as_list(fm.get("stack")),
        created=str(fm.get("created", "")),
        raw=fm,
    )


def validate_frontmatter(content: str) -> str | None:
    """Strict validation for writes. Returns error message or None if valid."""
    if not content.startswith("---"):
        return "Content must start with YAML frontmatter (---)."

    parts = content.split("---", 2)
    if len(parts) < 3:
        return "Malformed frontmatter: missing closing '---'."

    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        return f"Invalid YAML in frontmatter: {e}"

    if not isinstance(fm, dict):
        return "Frontmatter must be a YAML mapping."

    missing = _REQUIRED_FIELDS - fm.keys()
    if missing:
        return f"Frontmatter missing required fields: {', '.join(sorted(missing))}"

    return None


def extract_body(text: str) -> str:
    """Return markdown body without frontmatter."""
    if not text.startswith("---"):
        return text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return text

    return parts[2].lstrip("\n")


def parse_date(date_str: str) -> date | None:
    """Parse ISO date from a possibly quoted string. Returns None on failure."""
    cleaned = date_str.strip().strip("'\"")
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None
