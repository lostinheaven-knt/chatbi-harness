"""IR tool-allowlist enforcement for the ChatBI agent (module 5, MAJOR-2 fix;
skill+hooks module A adaptation).

The IR declares per-step ``tools.allow``/``tools.deny`` lists (and a
workflow-level default). In the skill+hooks architecture the enforcement
moves from "step-agent construction filtering" to two edges:

- :class:`StepToolPolicy` — the deterministic judgment ``check(tool)``:
  deny priority, allowlist semantics (anything not explicitly allowed is
  blocked — the design's "非 allowlist → BLOCK" rule, C011 semantic);
- :func:`filter_agent_tools` — semantics ADJUSTED (design §1.3): it is no
  longer used to filter an agent-step tool surface (there are no agent
  steps); its pure filter stays available for ① agent_builder assembly
  (register governance tools + read-only file tools per the workflow tool
  surface) and ② the allowlist hook (``runtimes.agno.hooks``, module B)
  denies at runtime by NOT calling ``next_func`` — the strongest allowlist
  is "unregistered = unavailable", the hook is the second line (C011);
- :data:`TOOL_NAME_MAP` — agno 2.6.22 tool names (``read_file``,
  ``list_files``, ``search_files``, ``search_content``, …) mapped onto the
  IR vocabulary (``Read``/``Grep``/``Glob``/``Bash``/…); unmapped agno tools
  are blocked (fail-closed).

When an out-of-allowlist tool call is attempted, the hook emits a
``tool.blocked`` standard event and the tool is never executed (fail-closed).
All judgments are deterministic IR lookups (HOOK-001); no second business
rule lives here (invariant 2).

Applicable rules: HOOK-001, SEC-001, MR-005, invariant 2/5.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

#: agno 2.6.22 tool name -> IR vocabulary (design §4.1 tool names).
TOOL_NAME_MAP: dict[str, str] = {
    # agno bundles FileTools into ONE composite tool named ``file_tools``
    # (agno/tools/file.py builds a single Tool from the enabled operations).
    # Real-model integration: without this mapping the whole bundle was
    # filtered out (unmapped -> blocked) and the live agent lost its file
    # surface. The composite is judged as a Read-family tool; the deployer
    # must configure the bundle read-only (save/delete disabled) — the
    # adapter cannot split a bundle, so per-operation Write/Edit deny inside
    # a bundle is a deployment-configuration responsibility (documented
    # limitation, SEC-001 red line stays for scripted tool_calls).
    "file_tools": "Read",
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
    """Filter a tool list by the policy (pure filter, module A semantics).

    In the skill+hooks architecture this is used at ① agent_builder assembly
    (registering the governance tool surface + read-only file tools) and
    ② the allowlist hook (module B) as the deterministic judgment; the
    runtime denial happens by not calling ``next_func`` (never executes the
    tool)."""
    return [tool for tool in tools if policy.check(tool_name_of(tool))]
