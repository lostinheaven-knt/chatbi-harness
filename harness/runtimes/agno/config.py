"""Agno deployment configuration: model_ref injection point (module 5, MR-D).

Adjudication seven (deployment design §9.2, §22): the Harness IR only
references a logical ``model_ref``; the actual model provider, API key and
billing account belong to the Agno deployment boundary. This module loads the
deployment-side startup config (a JSON file provided by the deployer, e.g. the
test venv's ``config.json``) and resolves ``model_ref`` → provider parameters.

Hard rules (invariant 5, SEC-003):

- API keys are read from the deployment config / environment ONLY at startup
  and are never written into IR, Evidence, events, sessions, traces, crontab
  or logs. This module never prints or serializes a key.
- Sanitized, non-sensitive runtime metadata (provider / model / token /
  latency) may be recorded in Evidence by the Adapter, never the key itself.
- A deployment config that is missing, malformed, or references an unknown
  model_ref is FAIL-CLOSED (MR-005): raise, do not fall back to defaults.

Applicable rules: MR-005, SEC-003, PORT-001, invariant 5, HOOK-001.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

#: Env var names the deployment config may use to inject secrets (never
#: hard-coded values in code/IR). The deployer owns the values.
ENV_PROVIDER = "CHATBI_AGNO_PROVIDER"
ENV_BASE_URL = "CHATBI_AGNO_BASE_URL"
ENV_API_KEY = "CHATBI_AGNO_API_KEY"
ENV_MODEL = "CHATBI_AGNO_MODEL"

#: Env vars read from the standard test deployment (agno-main/.venv/config.json
#: convention): DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY / DEEPSEEK_MODEL.
_FALLBACK_ENV = {
    "base_url": "DEEPSEEK_BASE_URL",
    "api_key": "DEEPSEEK_API_KEY",
    "model": "DEEPSEEK_MODEL",
}


@dataclass(frozen=True)
class ModelConfig:
    """Resolved model provider parameters (startup-only, never serialized)."""

    model_ref: str
    provider: str = "openai"          # agno model class family (openai/…)
    model: str = ""
    base_url: str = ""
    api_key: str = ""                 # startup-only; never persisted

    def sanitized_metadata(self) -> dict[str, str]:
        """Non-sensitive metadata safe for Evidence (no key, no URL)."""
        return {
            "provider": self.provider,
            "model": self.model,
        }


@dataclass(frozen=True)
class DeploymentConfig:
    """Deployment-side startup configuration (adjudication five/seven)."""

    config_path: Path | None = None
    models: Mapping[str, ModelConfig] = field(default_factory=dict)
    #: MVP single superuser subject (adjudication five) — the verified human
    #: Owner that may resolve ChatBI approvals. Read from deployment config,
    #: never from a request body.
    superuser_subject: str | None = None
    #: Trusted-auth mode: "jwt" (Agno authorization middleware) or "stub"
    #: (spike/test-only resolver). Default "stub" is documented as
    #: development-only; production requires a verified JWT boundary (module 6).
    auth_mode: str = "stub"
    #: Runtime state directory for product state (events, approvals, index).
    state_dir_name: str = ".chatbi-runtime"

    def model_config(self, model_ref: str) -> ModelConfig:
        try:
            return self.models[model_ref]
        except KeyError as error:
            raise RuntimeError(
                f"model_ref {model_ref!r} is not configured in the deployment "
                f"config {self.config_path}; the deployer must provide it "
                "(fail-closed, MR-005)"
            ) from error


def _read_key(path: Path) -> str:
    """Read an API key from the deployment config JSON, with a size cap."""
    raw = path.read_bytes()
    if len(raw) > 256 * 1024:
        raise ValueError(f"deployment config too large: {path}")
    return raw.decode("utf-8")


def _env_or(data: Mapping[str, Any], key: str, env_names: tuple[str, ...]) -> str:
    """Prefer the deployment JSON field, then the environment names."""
    value = data.get(key)
    if isinstance(value, str) and value:
        return value
    for name in env_names:
        env_value = os.environ.get(name)
        if env_value:
            return env_value
    return ""


def load_deployment_config(
    config_path: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
) -> DeploymentConfig:
    """Load and validate the deployment startup config (fail-closed).

    The JSON shape mirrors the test deployment (agno-main/.venv/config.json):
    ``{"provider": ..., "base_url": ..., "api_key": ..., "model": ...}`` with
    optional ``model_refs`` map, ``superuser_subject`` and ``auth_mode``.
    A single top-level model section configures the default ``model_ref``
    ("default"); a ``model_refs`` map overrides per-ref.

    ``env`` is injectable for tests; default = ``os.environ``.
    """
    os_env = env if env is not None else os.environ

    if config_path is None:
        # No deployment config: derive purely from environment variables.
        return DeploymentConfig(
            config_path=None,
            models=_models_from_env(os_env),
            superuser_subject=os_env.get("CHATBI_SUPERUSER_SUBJECT"),
            auth_mode=os_env.get("CHATBI_AUTH_MODE", "stub"),
        )

    path = Path(config_path)
    try:
        data = json.loads(_read_key(path))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        raise RuntimeError(
            f"deployment config {path} is missing or malformed (fail-closed): "
            f"{type(error).__name__}"
        ) from error
    if not isinstance(data, Mapping):
        raise RuntimeError(f"deployment config {path} must be a JSON object")

    models: dict[str, ModelConfig] = {}

    def _resolve(ref_name: str, section: Mapping[str, Any]) -> ModelConfig:
        model = _env_or(section, "model", (ENV_MODEL, _FALLBACK_ENV["model"]))
        base_url = _env_or(section, "base_url", (ENV_BASE_URL, _FALLBACK_ENV["base_url"]))
        api_key = _env_or(section, "api_key", (ENV_API_KEY, _FALLBACK_ENV["api_key"]))
        provider = section.get("provider") or "openai"
        if not isinstance(provider, str) or not provider:
            raise RuntimeError(f"model_ref {ref_name!r}: provider must be non-empty")
        return ModelConfig(
            model_ref=ref_name,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
        )

    refs = data.get("model_refs")
    if isinstance(refs, Mapping):
        for ref_name, section in refs.items():
            if not isinstance(section, Mapping):
                raise RuntimeError(f"model_refs.{ref_name} must be an object")
            models[ref_name] = _resolve(str(ref_name), section)
    if data.get("model") or data.get("api_key") or data.get("base_url"):
        models.setdefault("default", _resolve("default", data))

    superuser_subject = data.get("superuser_subject")
    if superuser_subject is not None and (
        not isinstance(superuser_subject, str) or not superuser_subject
    ):
        raise RuntimeError("superuser_subject must be a non-empty string")
    auth_mode = data.get("auth_mode", "stub")
    if auth_mode not in ("jwt", "stub"):
        raise RuntimeError(
            f"auth_mode {auth_mode!r} is not supported (jwt|stub)"
        )
    state_dir_name = data.get("state_dir_name", ".chatbi-runtime")
    if not isinstance(state_dir_name, str) or not state_dir_name:
        raise RuntimeError("state_dir_name must be a non-empty string")

    return DeploymentConfig(
        config_path=path,
        models=models,
        superuser_subject=superuser_subject,
        auth_mode=auth_mode,
        state_dir_name=state_dir_name,
    )


def _models_from_env(os_env: Mapping[str, str]) -> dict[str, ModelConfig]:
    model = os_env.get(ENV_MODEL) or os_env.get(_FALLBACK_ENV["model"], "")
    base_url = os_env.get(ENV_BASE_URL) or os_env.get(_FALLBACK_ENV["base_url"], "")
    api_key = os_env.get(ENV_API_KEY) or os_env.get(_FALLBACK_ENV["api_key"], "")
    provider = os_env.get(ENV_PROVIDER, "openai")
    if not model:
        return {}
    return {
        "default": ModelConfig(
            model_ref="default", provider=provider, model=model,
            base_url=base_url, api_key=api_key,
        )
    }
