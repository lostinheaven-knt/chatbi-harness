"""Runtime adapters for the ChatBI Harness (multi-runtime modules 2/4/5).

``runtimes/<target>/`` holds target-specific adapter code ONLY: the Claude
Code adapter (``claude_code``, modules 2/4) and the Agno adapter (``agno``,
module 5). Every governance judgment lives in ``chatbi_governance``
(invariant 2); nothing here may redefine a gate, approval, review or
evidence rule.

sys.path hygiene: prefer putting the harness ROOT on ``sys.path`` (so
``import runtimes.<target>`` resolves) instead of this directory — putting
``<root>/runtimes`` on the path makes the top-level name ``agno`` resolve to
``runtimes/agno`` and shadow the installed agno package.
"""
