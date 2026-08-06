"""Controlled condition-expression grammar for IR step gates.

The IR ``when`` field is a whitelist grammar only (design §4.4): no
Python/Shell ``eval``, no arbitrary functions, no undefined symbols. A
recursive-descent parser turns a valid expression into a tiny AST
(:class:`Cond`); anything outside the grammar is rejected.

Grammar::

    condition := "always" | "never"
               | "evidence.has_gap(<tier>)"          tier := "T1" | "T2"
               | "evidence.has(<tier>)"              tier := "T1" | "T2" | "T3"
               | "request.field_is(<field>,<value>)" field/value := literal
               | "owner.pending(<action>)"           action := one of the four
                                                       protected actions
               | "delivery_decision.is_pass"
               | "(" condition ")"

Literals are double-quoted strings or barewords (``request.field_is(segment,
undefined)``, design §4.3). Rejected explicitly: identifiers starting with
``__`` or ``.``, ``eval(``/``exec(``, ``;``, backticks, unknown functions,
wrong arity, and any leftover input.

Future condition functions must pass the deterministic-gate review
(HOOK-001) before being added here.

Applicable rules: HOOK-001, PORT-001.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schema import PROTECTED_ACTIONS


class ConditionSyntaxError(ValueError):
    """Raised when an expression does not conform to the whitelist grammar."""


#: Tier arguments accepted by ``evidence.has_gap`` / ``evidence.has``.
_TIERS_GAP = frozenset({"T1", "T2"})
_TIERS_HAS = frozenset({"T1", "T2", "T3"})

#: Bareword literal: letters/digits/underscore/hyphen, not starting with __ or .
_BAREWORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

#: Single-quoted or double-quoted string literal (no embedded quote, no escapes).
_QUOTED = re.compile(r'^"(?:[^"\\]|\\.)*"$|^\'(?:[^\'\\]|\\.)*\'$')

_TOKEN = re.compile(r"\s*(\(|\)|,|\.|[\"'][^\"']*[\"']|[A-Za-z_][A-Za-z0-9_-]*)")
_ATOM_RE = re.compile(r"^(always|never|delivery_decision|evidence|request|owner)$")


@dataclass(frozen=True)
class Cond:
    """Condition AST node.

    ``kind`` is one of: ``always``, ``never``, ``has_gap``, ``has``,
    ``field_is``, ``owner_pending``, ``delivery_is_pass``, ``group``.
    ``args`` holds the literal arguments (e.g. ``("T1",)`` for ``has_gap``,
    ``("segment", "undefined")`` for ``field_is``).
    """

    kind: str
    args: tuple = ()


def _tokens(expr: str) -> list[str]:
    """Tokenize the expression; raise on any non-grammar character.

    Whitespace between tokens is allowed. Any other character (``;``,
    backticks, ``{``, ``!``, …) is a syntax error — this is the hard
    anti-``eval`` boundary.
    """
    pos = 0
    tokens: list[str] = []
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if m is None or m.end() == pos:
            bad = expr[pos:pos + 1]
            raise ConditionSyntaxError(
                f"illegal character {bad!r} at offset {pos} in condition {expr!r}"
            )
        tok = m.group(1).strip() if m.group(1) else m.group(0)
        if tok:
            if tok.startswith("__"):
                raise ConditionSyntaxError(
                    f"identifier starting with '__' is not allowed: {tok!r}"
                )
            # A dot-leading identifier ("." glued to a name) is rejected; the
            # bare "." token itself is the grammar's path separator (e.g.
            # evidence.has_gap) and is handled by the parser productions.
            if tok.startswith(".") and tok != ".":
                raise ConditionSyntaxError(
                    f"identifier starting with '.' is not allowed: {tok!r}"
                )
            tokens.append(tok)
        pos = m.end()
    return tokens


def _strip_quotes(token: str) -> str:
    if token[:1] in ("'", '"') and token[-1:] == token[:1]:
        return token[1:-1]
    return token


def _parse_primary(tokens: list[str], pos: int) -> tuple[Cond, int]:
    """Parse one primary condition from ``tokens`` starting at ``pos``.

    Returns ``(node, next_pos)``; raises :class:`ConditionSyntaxError` on any
    deviation from the grammar.
    """
    if pos >= len(tokens):
        raise ConditionSyntaxError("unexpected end of condition")
    tok = tokens[pos]

    if tok == "always":
        return Cond("always"), pos + 1
    if tok == "never":
        return Cond("never"), pos + 1

    if tok == "(":
        node, npos = _parse_primary(tokens, pos + 1)
        if npos >= len(tokens) or tokens[npos] != ")":
            raise ConditionSyntaxError("unbalanced '(' in condition")
        return Cond("group", (node,)), npos + 1

    if tok == "evidence":
        if pos + 2 >= len(tokens) or tokens[pos + 1] != ".":
            raise ConditionSyntaxError(
                "expected '.' after 'evidence' in condition"
            )
        fn = tokens[pos + 2]
        if fn not in ("has_gap", "has"):
            raise ConditionSyntaxError(f"unknown evidence function {fn!r}")
        tier, npos = _parse_call_arg(tokens, pos + 3, "evidence")
        if fn == "has_gap" and tier not in _TIERS_GAP:
            raise ConditionSyntaxError(
                f"evidence.has_gap tier must be T1 or T2, got {tier!r}"
            )
        if fn == "has" and tier not in _TIERS_HAS:
            raise ConditionSyntaxError(
                f"evidence.has tier must be T1, T2 or T3, got {tier!r}"
            )
        return Cond("has_gap" if fn == "has_gap" else "has", (tier,)), npos

    if tok == "request":
        if pos + 2 >= len(tokens) or tokens[pos + 1] != ".":
            raise ConditionSyntaxError(
                "expected '.' after 'request' in condition"
            )
        if tokens[pos + 2] != "field_is":
            raise ConditionSyntaxError(
                f"unknown request function {tokens[pos + 2]!r}"
            )
        # request.field_is(<field>,<value>): the first argument is
        # parenthesized, then a comma, then the value literal, then ')'.
        if pos + 3 >= len(tokens) or tokens[pos + 3] != "(":
            raise ConditionSyntaxError(
                "request.field_is requires a parenthesized (field, value)"
            )
        field = _literal_arg(tokens, pos + 4, "request.field_is")
        vpos = pos + 5
        if vpos >= len(tokens) or tokens[vpos] != ",":
            raise ConditionSyntaxError(
                "request.field_is requires (field, value)"
            )
        value = _literal_arg(tokens, vpos + 1, "request.field_is")
        npos = vpos + 2
        if npos >= len(tokens) or tokens[npos] != ")":
            raise ConditionSyntaxError(
                "request.field_is missing closing ')'"
            )
        return Cond("field_is", (field, value)), npos + 1

    if tok == "owner":
        if pos + 2 >= len(tokens) or tokens[pos + 1] != ".":
            raise ConditionSyntaxError("expected '.' after 'owner' in condition")
        if tokens[pos + 2] != "pending":
            raise ConditionSyntaxError(
                f"unknown owner function {tokens[pos + 2]!r}"
            )
        action, npos = _parse_call_arg(tokens, pos + 3, "owner.pending")
        if action not in PROTECTED_ACTIONS:
            raise ConditionSyntaxError(
                f"owner.pending action must be one of {PROTECTED_ACTIONS}, "
                f"got {action!r}"
            )
        return Cond("owner_pending", (action,)), npos

    if tok == "delivery_decision":
        if pos + 2 >= len(tokens) or tokens[pos + 1] != ".":
            raise ConditionSyntaxError(
                "expected '.' after 'delivery_decision' in condition"
            )
        if tokens[pos + 2] != "is_pass":
            raise ConditionSyntaxError(
                f"unknown delivery_decision attribute {tokens[pos + 2]!r}"
            )
        return Cond("delivery_is_pass"), pos + 3

    raise ConditionSyntaxError(f"unknown condition symbol {tok!r}")


def _literal_arg(tokens: list[str], pos: int, fn: str) -> str:
    """Parse one literal argument token (quoted string or bareword)."""
    if pos >= len(tokens):
        raise ConditionSyntaxError(f"{fn} requires a literal argument")
    arg = tokens[pos]
    if arg in (")", ",", "(", "."):
        raise ConditionSyntaxError(
            f"{fn} requires a literal argument, got {arg!r}"
        )
    if not (_QUOTED.match(arg) or _BAREWORD.match(arg)):
        raise ConditionSyntaxError(
            f"{fn} argument must be a quoted string or bareword literal, "
            f"got {arg!r}"
        )
    return _strip_quotes(arg)


def _parse_call_arg(tokens: list[str], pos: int, fn: str) -> tuple[str, int]:
    """Parse one parenthesized literal argument of ``fn`` at ``pos``."""
    if pos >= len(tokens) or tokens[pos] != "(":
        raise ConditionSyntaxError(f"{fn} requires a parenthesized argument")
    if pos + 1 >= len(tokens):
        raise ConditionSyntaxError(f"{fn} requires an argument")
    arg = _literal_arg(tokens, pos + 1, fn)
    if pos + 2 >= len(tokens) or tokens[pos + 2] != ")":
        raise ConditionSyntaxError(f"{fn} missing closing ')'")
    return arg, pos + 3


def parse_condition(expr: str) -> Cond:
    """Parse a condition expression into an AST node.

    Raises :class:`ConditionSyntaxError` if the expression deviates from the
    whitelist grammar in any way.
    """
    if not isinstance(expr, str):
        raise ConditionSyntaxError(f"condition must be a string, got {expr!r}")
    tokens = _tokens(expr)
    if not tokens:
        raise ConditionSyntaxError("empty condition expression")
    node, pos = _parse_primary(tokens, 0)
    if pos != len(tokens):
        raise ConditionSyntaxError(
            f"trailing input {tokens[pos:]!r} in condition {expr!r}"
        )
    return node


def validate_condition(expr: str) -> bool:
    """True iff ``expr`` conforms to the controlled condition grammar."""
    try:
        parse_condition(expr)
        return True
    except ConditionSyntaxError:
        return False
