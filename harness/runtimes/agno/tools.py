"""IR tool-allowlist enforcement for Agno agent steps (module 5, MAJOR-2 fix).

The analyze IR declares per-step ``tools.allow``/``tools.deny`` lists (and a
workflow-level default). The Adapter enforces them at the agent-step
execution boundary:

- :class:`StepToolPolicy` — the deterministic judgment ``check(tool)``:
  deny priority, allowlist semantics (anything not explicitly allowed is
  blocked — the design's "非 allowlist → BLOCK" rule, C011 semantic);
- :func:`filter_agent_tools` — the REAL agno mechanism for live mode: the
  step agent is constructed with its tool surface filtered by the policy, so
  the runtime physically cannot invoke a non-allowlisted tool;
- :data:`TOOL_NAME_MAP` — agno 2.6.22 tool names (``read_file``,
  ``list_files``, ``search_files``, ``search_content``, …) mapped onto the
  IR vocabulary (``Read``/``Grep``/``Glob``/``Bash``/…); unmapped agno tools
  are blocked (fail-closed).

When an out-of-allowlist tool call is attempted, the adapter emits a
``tool.blocked`` standard event and the run fails fail-closed (the tool is
never executed). All judgments are deterministic IR lookups (HOOK-001);
no second business rule lives here (invariant 2).

Applicable rules: HOOK-001, SEC-001, MR-005, invariant 2/5.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: agno 2.6.22 tool name -> IR vocabulary (design §4.1 tool names).
TOOL_NAME_MAP: dict[str, str] = {
    "read_file": "Read",
    "read_file_chunk": "Read",
    "list_files": "Glob",
    "search_files": "Grep",
    "search_content": "Grep",
    "bash": "Bash",
    "run_shell": "Bash",
    "run": "Bash",
    "write_file": "Write",
    "replace_file_chunk": "Write",
    "save_file": "Write",
    "delete_file": "Delete",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
}


def _normalize(name: str) -> str:
    return TOOL_NAME_MAP.get(name, name)


class StepToolPolicy:
    """Deterministic allow/deny judgment for one IR agent step."""

    def __init__(self, allow: Iterable[str] = (), deny: Iterable[str] = ()) -> None:
        self.allow = frozenset(allow)
        self.deny = frozenset(deny)

    def check(self, tool_name: str) -> bool:
        """True = the tool may run; False = blocked (deny priority)."""
        name = _normalize(tool_name)
        if name in self.deny:
            return False
        if name in self.allow:
            return True
        # Allowlist semantics: an undeclared tool is blocked (C011).
        return False

    def allowed_tools(self, names: Iterable[str]) -> list[str]:
        """The subset of ``names`` permitted by this policy (live filter)."""
        return [name for name in names if self.check(name)]

    @classmethod
    def from_ir_step(cls, step: Any, workflow_tools: Any = None) -> "StepToolPolicy":
        """Build the policy from an IR step's ``tools`` spec, falling back to
        the workflow-level default when the step declares none."""
        step_tools = getattr(step, "tools", None)
        spec = step_tools if step_tools is not None else workflow_tools
        if spec is None:
            return cls()
        return cls(allow=getattr(spec, "allow", ()),
                   deny=getattr(spec, "deny", ()))


def tool_name_of(tool: Any) -> str:
    """Extract the tool name from an agno tool/function object."""
    name = getattr(tool, "name", None)
    if not name:
        name = getattr(tool, "__name__", None)
    return _normalize(str(name)) if name else ""


def filter_agent_tools(tools: Iterable[Any], policy: StepToolPolicy) -> list[Any]:
    """Filter an agno tool list by the policy (live-mode enforcement)."""
    return [tool for tool in tools if policy.check(tool_name_of(tool))]
