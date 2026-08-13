"""Deployment-boundary binding resolution (PORT-001 / SEC-003).

Machine-specific bindings — the agno installation root, the SEC-003 test
credential file, CLI binaries, acceptance fixture roots — belong ONLY at the
deployment boundary: environment variables or the workspace
``deployment.json``. They never belong in shared product config, product
code, or committed artifacts.

Every resolver here is fail-closed: when neither env nor deployment.json
supplies a required binding, it exits with a PORT-001 message instead of
falling back to a default machine path.

Stdlib-only on purpose: launchers and acceptance drivers call these helpers
before the workspace ``sys.path`` is fully wired, and the module must work
with or without the ``agno`` package installed.
"""

import json
import os
from pathlib import Path


def _deployment_json(ws: Path) -> dict:
    """Raw deployment.json content (deployment boundary, not validated config).

    Returns ``{}`` when the file is absent or unreadable — callers stay
    fail-closed on the missing binding instead of crashing on the read.
    """
    dep = ws / "deployment.json"
    if not dep.is_file():
        return {}
    try:
        return json.loads(dep.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_agno_main(ws: Path) -> Path:
    """Resolve the agno installation root (PORT-001, fail-closed).

    Order: ``CHATBI_AGNO_MAIN`` env var, then the deployment.json
    ``agno_main`` field (deployment boundary). Neither -> SystemExit with a
    PORT-001 message; no default fallback.
    """
    env_main = os.environ.get("CHATBI_AGNO_MAIN", "").strip()
    if env_main:
        return Path(env_main)
    main = str(_deployment_json(ws).get("agno_main") or "").strip()
    if main:
        return Path(main)
    raise SystemExit(
        "FATAL: agno installation root is not configured (PORT-001): set "
        "CHATBI_AGNO_MAIN=<agno-main-root> or add \"agno_main\": "
        "\"<agno-main-root>\" to deployment.json. Machine paths belong in "
        "the deployment boundary (env / deployment.json), never in shared "
        "product config."
    )


def load_credential_env(agno_main: Path) -> None:
    """Load SEC-003 test credentials into env vars ONLY (never persisted).

    Reads ``<agno_main>/.venv/config.json`` and ``setdefault``s the
    DEEPSEEK_* entries (the config loader's ``_env_or`` fallback consumes
    them). Fail-closed when the file is missing or unreadable.
    """
    creds_path = agno_main / ".venv" / "config.json"
    if not creds_path.is_file():
        raise SystemExit(
            f"FATAL: test credentials missing: {creds_path} (SEC-003)")
    try:
        creds = json.loads(creds_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"FATAL: unreadable credential file: {creds_path} "
            f"(SEC-003): {exc}")
    for key in ("DEEPSEEK_MODEL", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"):
        if creds.get(key):
            os.environ.setdefault(key, str(creds[key]))


def resolve_workspace_binding(ws: Path, env_name: str, json_field: str,
                              description: str) -> str:
    """Resolve a required deployment-boundary binding (fail-closed).

    Order: ``env_name`` env var, then the deployment.json ``json_field``.
    Neither -> SystemExit with a PORT-001 message naming the binding.
    """
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    raw = str(_deployment_json(ws).get(json_field) or "").strip()
    if raw:
        return raw
    raise SystemExit(
        f"FATAL: {description} is not configured (PORT-001): set "
        f"{env_name}=<path> or add \"{json_field}\" to deployment.json. "
        "Machine paths belong in the deployment boundary (env / "
        "deployment.json), never in shared product config."
    )


def resolve_cli_allowlist_entry(ws: Path, needle: str) -> str:
    """Resolve a CLI binary from deployment.json ``cli_allowlist``.

    ``needle`` matches a substring of the entry's basename (e.g. "mysql").
    No match -> SystemExit (PORT-001, fail-closed). The allowlist itself is
    the deployment authority (MR-005): callers never fall back to a bare
    command name.
    """
    for entry in _deployment_json(ws).get("cli_allowlist") or []:
        name = str(entry)
        if needle in Path(name).name:
            return name
    raise SystemExit(
        f"FATAL: no cli_allowlist entry matches {needle!r} in "
        f"{ws / 'deployment.json'} (PORT-001, fail-closed)."
    )
