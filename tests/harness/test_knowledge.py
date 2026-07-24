"""Cycle 4 Task 03: knowledge-reference lint/retrieval contract tests.

Tests cover (per ticket 03-maintain-knowledge-skill-template.md): required
metadata, "use for"/"do not use for", machine absolute paths, duplicate/conflict,
historical SQL `candidate_only`, and cross-references. The template and
fixture-domain references must pass lint; deliberately-bad references must fail.

Applicable rules: DOC-001/002/003/005, SEM-003, SRC-001/002, PORT-001, SEC-003.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
HARNESS_LIB = WORKSPACE_ROOT / ".claude" / "lib"
sys.path.insert(0, str(HARNESS_LIB))

from chatbi_harness.knowledge import (  # noqa: E402
    REQUIRED_FIELDS,
    LintIssue,
    lint_reference,
)

REFS_DIR = (WORKSPACE_ROOT / ".claude" / "skills" / "chatbi-knowledge"
            / "references")
TEMPLATE = (REFS_DIR / "_template.md").read_text()
FIXTURE_DOMAIN = (REFS_DIR / "fixture-domain.md").read_text()


def _issue_categories(issues: tuple[LintIssue, ...]) -> set[str]:
    return {i.category for i in issues}


def _strip_section(text: str, header: str) -> str:
    """Remove a header and its body (until the next header)."""
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.lstrip()
        is_header = stripped.startswith("#")
        title = stripped.lstrip("#").strip() if is_header else ""
        if is_header and skipping:
            skipping = False
        if title == header:
            skipping = True
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out)


class TemplateAndFixtureLintTests(unittest.TestCase):
    def test_template_passes_lint(self) -> None:
        self.assertEqual((), lint_reference(TEMPLATE))

    def test_fixture_domain_passes_lint(self) -> None:
        self.assertEqual((), lint_reference(FIXTURE_DOMAIN))

    def test_required_fields_non_empty_and_present_in_template(self) -> None:
        self.assertGreater(len(REQUIRED_FIELDS), 10)
        for field in REQUIRED_FIELDS:
            self.assertIn(f"## {field}", TEMPLATE, f"template missing {field}")


class LintFailureTests(unittest.TestCase):
    def test_missing_do_not_use_for_fails(self) -> None:
        bad = _strip_section(TEMPLATE, "Do not use for")
        issues = lint_reference(bad)
        cats = _issue_categories(issues)
        self.assertIn("missing", cats)

    def test_empty_do_not_use_for_fails(self) -> None:
        bad = TEMPLATE.replace(
            "<Trigger conditions: do NOT use this reference when ...; e.g. real-time, PII-level.>",
            "  ")
        # Ensure the section header is present but body is blank.
        issues = lint_reference(bad)
        cats = _issue_categories(issues)
        self.assertIn("empty", cats)

    def test_absolute_path_fails(self) -> None:
        bad = TEMPLATE + "\n\nsee /Users/admin/secret/path.md\n"
        issues = lint_reference(bad)
        cats = _issue_categories(issues)
        self.assertIn("path", cats)

    def test_historical_sql_without_candidate_only_fails(self) -> None:
        bad = FIXTURE_DOMAIN.replace("candidate_only", "canonical")
        # Remove the candidate_only marker comment too.
        bad = bad.replace("-- historical reporting clue, not the canonical definition",
                          "-- historical clue")
        issues = lint_reference(bad)
        cats = _issue_categories(issues)
        self.assertIn("raw-sql", cats)

    def test_historical_sql_with_candidate_only_passes(self) -> None:
        # Fixture domain already has candidate_only and passes.
        self.assertEqual((), lint_reference(FIXTURE_DOMAIN))

    def test_duplicate_header_fails(self) -> None:
        bad = TEMPLATE + "\n\n## Owner\n\n<duplicate>\n"
        issues = lint_reference(bad)
        cats = _issue_categories(issues)
        self.assertIn("duplicate", cats)

    def test_empty_cross_references_fails(self) -> None:
        bad = TEMPLATE.replace(
            "- related/reference-example.md\n- models/revenue_example.sql",
            "(none)")
        issues = lint_reference(bad)
        cats = _issue_categories(issues)
        self.assertIn("empty", cats)

    def test_empty_reference_fails(self) -> None:
        issues = lint_reference("")
        self.assertTrue(any(i.category == "empty" for i in issues))

    def test_no_sql_no_candidate_only_needed(self) -> None:
        # A reference with no SQL block needs no candidate_only marker.
        text = TEMPLATE.replace("```sql\n-- candidate_only: example historical clue, not a canonical definition\nSELECT ... FROM ...\n```",
                                "(no historical SQL for this reference)")
        # Removing the SQL block should still pass (no SQL -> no marker needed).
        self.assertEqual((), lint_reference(text))


if __name__ == "__main__":
    unittest.main()
