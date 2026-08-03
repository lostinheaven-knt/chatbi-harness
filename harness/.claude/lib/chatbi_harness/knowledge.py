"""Cycle 4 Task 03: knowledge-reference lint.

Knowledge references co-located with the Warehouse model must carry governed
metadata and explicit "use for" / "do not use for" triggers (DOC-002/003).
``lint_reference`` checks a reference document for required fields, machine
absolute paths (PORT-001), historical-SQL-as-canonical misuse (RAW/SRC), and
duplicate / conflicting structure. It returns issues; an empty tuple means the
reference is route-ready. It never mutates the document.

Applicable rules: DOC-001/002/003/005, SEM-003, SRC-001/002, PORT-001, SEC-003.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Required reference fields (DOC-002/003). Each must appear as a markdown header.
REQUIRED_FIELDS: tuple[str, ...] = (
    "Business context",
    "Grain",
    "Standard filters",
    "Dimensions",
    "Key models",
    "Scope and exclusions",
    "Joins",
    "Common pitfalls",
    "Best practices",
    "Cross-references",
    "Owner",
    "Freshness",
    "Use for",
    "Do not use for",
)

# Machine absolute paths are never allowed in shared references (PORT-001).
_ABSOLUTE_PATH = re.compile(r"/(?:Users|home|private|etc|var|root)/[A-Za-z0-9._\-/]+")

# Historical SQL / notebook / dashboard queries are candidate clues only. If a
# SQL block is present it must carry the candidate_only marker (RAW-001/002,
# SRC-001/002).
_SQL_FENCE = re.compile(r"```(?:sql|SQL)?", re.MULTILINE)
_CANDIDATE_ONLY = re.compile(r"candidate[_ -]?only", re.IGNORECASE)

# Optional `## Citation` section (DOC-002, design gap 1 / OD1). NOT a required
# field - it is absent from REQUIRED_FIELDS so existing references still lint.
# When present with a *filled* git_sha (a real value, not a template `<...>`
# placeholder), lint validates the shape: alias + relative_path + 40/64-hex
# git_sha. An absent/empty/placeholder git_sha is the c-bridge skipped state
# (citation is optional), not a lint error. The `/chatbi-audit-drift` class 1
# check compares this cited git_sha against the codebase alias's current HEAD.
_CITATION_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_CITATION_LINE = re.compile(
    r"(?m)^\s*-\s+\*{0,2}(?P<key>alias|relative_path|git_sha|captured_at)\*{0,2}"
    r"\s*:\s*(?P<val>.+?)\s*$"
)


@dataclass(frozen=True, slots=True)
class LintIssue:
    """One knowledge-reference lint finding."""

    category: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"[{self.category}] {self.field}: {self.message}"


def _headers(text: str) -> list[str]:
    """Markdown headers (``## Title``) in order, stripped of leading ``#``."""
    headers: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            headers.append(stripped.lstrip("#").strip())
    return headers


def lint_reference(text: str) -> tuple[LintIssue, ...]:
    """Lint a knowledge reference. Returns issues (empty = route-ready).

    Checks: required fields present; no machine absolute paths; historical SQL
    marked ``candidate_only``; no duplicate headers; cross-references non-empty.
    Fail-closed semantics: a missing or unresolvable field is an issue, never a
    silent pass. The document is never mutated.
    """
    issues: list[LintIssue] = []
    if not text or not text.strip():
        return (LintIssue("empty", "document", "reference is empty"),)

    headers = _headers(text)
    header_set: dict[str, int] = {}
    for h in headers:
        header_set[h] = header_set.get(h, 0) + 1
    for h, count in header_set.items():
        if count > 1:
            issues.append(LintIssue("duplicate", h, f"header appears {count} times"))

    for field in REQUIRED_FIELDS:
        if field not in header_set:
            issues.append(LintIssue("missing", field, "required field is absent"))

    # Cross-references must list at least one neighbor reference (DOC-001).
    if "Cross-references" in header_set:
        cr_block = _section_block(text, "Cross-references")
        if not re.search(r"[A-Za-z0-9_\-/]+\.(?:md|sql|json)", cr_block):
            issues.append(LintIssue("empty", "Cross-references",
                                    "no neighbor reference listed"))

    # "Do not use for" must be non-empty (DOC-003).
    if "Do not use for" in header_set:
        block = _section_block(text, "Do not use for")
        if len(block.strip()) < 3:
            issues.append(LintIssue("empty", "Do not use for",
                                    "trigger conditions must be stated, not blank"))

    for match in _ABSOLUTE_PATH.finditer(text):
        issues.append(LintIssue("path", "document",
                                f"machine absolute path: {match.group(0)}"))

    if _SQL_FENCE.search(text) and not _CANDIDATE_ONLY.search(text):
        issues.append(LintIssue("raw-sql", "document",
                                "historical SQL present without candidate_only marker"))

    # Optional `## Citation` section (DOC-002, OD1). Validate shape only when a
    # filled git_sha is present; an absent/placeholder citation is not an error.
    if "Citation" in header_set:
        citation_fields: dict[str, str] = {}
        for _m in _CITATION_LINE.finditer(_section_block(text, "Citation")):
            citation_fields[_m.group("key")] = _m.group("val").strip()
        git_sha = citation_fields.get("git_sha", "")
        if git_sha and not git_sha.startswith("<"):
            if not _CITATION_SHA.fullmatch(git_sha):
                issues.append(LintIssue("citation", "Citation",
                                        "git_sha must be 40/64 hex characters"))
            alias = citation_fields.get("alias", "")
            relative_path = citation_fields.get("relative_path", "")
            if not alias or alias.startswith("<"):
                issues.append(LintIssue("citation", "Citation",
                                        "alias is required when a citation git_sha is present"))
            if not relative_path or relative_path.startswith("<"):
                issues.append(LintIssue("citation", "Citation",
                                        "relative_path is required when a citation git_sha is present"))

    return tuple(issues)


def _section_block(text: str, header: str) -> str:
    """Return the body text under a markdown header (until the next header)."""
    lines = text.splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.lstrip()
        is_header = stripped.startswith("#")
        title = stripped.lstrip("#").strip() if is_header else ""
        if in_section and is_header:
            break
        if in_section:
            out.append(line)
        if title == header:
            in_section = True
    return "\n".join(out)


__all__ = ["LintIssue", "REQUIRED_FIELDS", "lint_reference"]
