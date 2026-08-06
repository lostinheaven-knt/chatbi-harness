"""Runtime capability contract: status five-class, manifest, probe (module 3).

The five-class capability model (design §6.2):

- ``required``: missing => deployment is refused (MR-005 fail-closed);
- ``provided_by_runtime``: the Runtime offers it natively;
- ``provided_by_adapter``: the Adapter provides it and it passed tests;
- ``partial``: development/synthetic acceptance only, never production;
- ``unsupported``: explicitly refused.

``missing_required`` implements the fail-closed judgment used by Adapter
startup and ``doctor --target <t>``: every IR ``requirements`` entry at
level ``required`` must be provided by the Runtime or the Adapter, otherwise
the deployment must not start (design §6.2, MR-005). ``protected_actions``
level does not block — the Claude target legitimately runs with a
``partial`` human-approval capability in development mode (adjudication
six).

Applicable rules: MR-005, PORT-001, invariant 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol


class CapabilityStatus(str, Enum):
    REQUIRED = "required"
    PROVIDED_BY_RUNTIME = "provided_by_runtime"
    PROVIDED_BY_ADAPTER = "provided_by_adapter"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


#: Statuses that fail a ``required`` capability (MR-005 fail-closed).
_UNSATISFIED = frozenset(
    {CapabilityStatus.PARTIAL, CapabilityStatus.UNSUPPORTED}
)

_CAPABILITY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Requirement levels from IR (schema.RequirementLevel) that demand a
#: provided capability at deployment time.
_REQUIRED_LEVELS = frozenset({"required"})


@dataclass(frozen=True)
class CapabilityEntry:
    status: CapabilityStatus
    modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityManifest:
    runtime: str
    runtime_version: str
    capabilities: Mapping[str, CapabilityEntry]
    issued_by: str
    generated_at: str

    def validate(self) -> list[str]:
        """Return violation strings; [] = well-formed manifest."""
        errors: list[str] = []
        if not self.runtime:
            errors.append("runtime: must be non-empty")
        if not self.runtime_version:
            errors.append("runtime_version: must be non-empty")
        if not self.issued_by:
            errors.append("issued_by: must be non-empty")
        if not self.generated_at:
            errors.append("generated_at: must be non-empty")
        for name, entry in self.capabilities.items():
            if not _CAPABILITY_NAME_RE.match(name):
                errors.append(f"capabilities.{name}: invalid capability name")
            if not isinstance(entry, CapabilityEntry):
                errors.append(f"capabilities.{name}: not a CapabilityEntry")
                continue
            if not isinstance(entry.status, CapabilityStatus):
                errors.append(
                    f"capabilities.{name}: invalid status {entry.status!r}"
                )
            for mode in entry.modes:
                if not isinstance(mode, str) or not mode:
                    errors.append(
                        f"capabilities.{name}.modes: entries must be "
                        "non-empty strings"
                    )
        return errors

    def missing_required(self, requirements: Mapping[str, str]) -> list[str]:
        """Fail-closed judgment: IR requirements not satisfiable.

        ``requirements`` maps capability name -> IR level
        (``required``/``optional``/``protected_actions``/``none``). Every
        entry at level ``required`` must be present in the manifest with
        status ``provided_by_runtime`` or ``provided_by_adapter``; anything
        else is reported so the caller can refuse to deploy (MR-005).
        """
        missing: list[str] = []
        for name, level in requirements.items():
            if level not in _REQUIRED_LEVELS:
                continue
            entry = self.capabilities.get(name)
            if entry is None:
                missing.append(
                    f"required capability {name!r} is not declared by "
                    f"{self.runtime}"
                )
                continue
            if entry.status in _UNSATISFIED:
                missing.append(
                    f"required capability {name!r} is {entry.status.value} "
                    f"on {self.runtime} (only provided_by_runtime/"
                    "provided_by_adapter satisfy a required level)"
                )
        return missing


class CapabilityProbe(Protocol):
    """A Runtime capability probe: produces the manifest."""

    def probe(self) -> CapabilityManifest: ...
