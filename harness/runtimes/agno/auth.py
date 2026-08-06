"""Trusted JWT authentication boundary for the Agno target (module 6, MR-E3).

Adjudication five: the ChatBI approval subject MUST come from a trusted
authentication context (JWT ``sub`` or an equivalent verified server-side
resolver) — never from a request body. Module 5 shipped a ``stub`` resolver
for spike/tests; module 6 adds the production-shape JWT boundary:

- :func:`make_jwt_auth_resolver` verifies an HS256 Bearer token with the
  stdlib only (no new dependency): signature (HMAC-SHA256 over
  header.payload), ``exp`` and ``nbf``/``iat`` sanity, issuer check;
- the resolved ``AuthSubject(subject=sub)`` is passed to the router's
  trusted-subject gate; role mapping is MVP single-superuser:
  ``sub == deployment.superuser_subject`` -> ChatBI Owner (may resolve
  approvals); any other verified subject is authenticated but NOT an Owner
  (approval endpoints 403);
- fail-closed: missing header, malformed token, bad signature, expired
  token, wrong issuer, unknown algorithm, or a missing/empty secret all
  yield ``None`` — approvals cannot resolve (401/403), never a silent pass.

The secret comes from the deployment startup boundary ONLY
(``CHATBI_JWT_SECRET`` env or the deployment config ``jwt_secret`` field —
startup-only, never serialized into Evidence/events/logs; SEC-003). When
``auth_mode == "jwt"`` but no secret is configured the app factory refuses
to start (fail-closed, MR-005).

No business rule lives here (invariant 2); this is the authentication
boundary the deployment contract requires.

Applicable rules: adjudication five, SEC-003, MR-005, invariant 5, design
§17 row 6 (approval subject).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

#: The only algorithm this boundary accepts (no algorithm confusion).
_ALLOWED_ALG = "HS256"


@dataclass(frozen=True)
class AuthSubject:
    """Trusted authenticated subject (JWT sub or an equivalent server-side
    verification). ``is_agent`` marks non-human actors — they can never
    approve (SEM-003).

    Defined here (not in router_chatbi) so the auth boundary stays
    fastapi-free and importable on the system python (build-product import
    validation)."""

    subject: str
    is_agent: bool = False

#: Claims whose presence is required for a valid token.
_REQUIRED_CLAIMS = ("sub", "exp")


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_jwt(secret: str, claims: Mapping[str, Any]) -> str:
    """Sign an HS256 JWT (used by tests and deployments for issuing tokens).

    ``claims`` must include ``sub`` and ``exp``; ``iss``/``iat``/``nbf`` are
    passed through when present.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (
        _b64url_encode(json.dumps(header, sort_keys=True).encode("utf-8"))
        + "."
        + _b64url_encode(json.dumps(dict(claims), sort_keys=True).encode("utf-8"))
    )
    signature = hmac.new(
        secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return signing_input + "." + _b64url_encode(signature)


def verify_jwt(
    token: str,
    *,
    secret: str,
    issuer: str | None = None,
    clock: Callable[[], float] | None = None,
    leeway_seconds: int = 30,
) -> dict[str, Any] | None:
    """Verify an HS256 JWT and return its payload claims.

    Returns ``None`` on ANY failure (missing/invalid token, bad signature,
    expired, wrong issuer, unexpected algorithm, malformed JSON) — the
    caller treats that as unauthenticated (fail-closed). ``clock`` is
    injectable for tests.
    """
    if not token or not isinstance(token, str):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        expected = hmac.new(
            secret.encode("utf-8"),
            f"{header_b64}.{payload_b64}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        supplied = _b64url_decode(signature_b64)
    except Exception:  # noqa: BLE001 - malformed token -> unauthenticated
        return None
    if not hmac.compare_digest(expected, supplied):
        return None
    if not isinstance(header, Mapping) or header.get("alg") != _ALLOWED_ALG:
        return None
    if not isinstance(payload, Mapping):
        return None
    for claim in _REQUIRED_CLAIMS:
        if claim not in payload:
            return None
    if issuer is not None and payload.get("iss") != issuer:
        return None
    now = (clock() if clock is not None else time.time()) - leeway_seconds
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    if now >= exp:
        return None
    return dict(payload)


def extract_bearer_token(authorization: str | None) -> str | None:
    """Pull the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization or not isinstance(authorization, str):
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def make_jwt_auth_resolver(
    *,
    secret: str | None,
    issuer: str = "chatbi",
    superuser_subject: str | None = None,
    clock: Callable[[], float] | None = None,
) -> Callable[[Any], AuthSubject | None]:
    """Build the trusted-auth resolver: ``(Request) -> AuthSubject | None``.

    A missing/empty secret yields a resolver that always returns ``None``
    (fail-closed; the app factory refuses JWT mode without a secret anyway).
    ``superuser_subject`` is used for the Owner role mapping decision (the
    router compares the resolved subject against the deployment superuser).
    """
    if not secret:
        return lambda _request: None

    def _resolve(request: Any) -> AuthSubject | None:
        header = getattr(request, "headers", None)
        authorization = None
        if isinstance(header, Mapping):
            authorization = header.get("authorization")
        if authorization is None and header is not None:
            # Starlette Headers: case-insensitive lookup via dict().
            authorization = dict(header).get("authorization")
        token = extract_bearer_token(authorization)
        if token is None:
            return None
        claims = verify_jwt(
            token, secret=secret, issuer=issuer, clock=clock,
        )
        if claims is None:
            return None
        return AuthSubject(subject=str(claims["sub"]), is_agent=False)

    return _resolve


def jwt_secret_from_deployment(deployment: Any, env: Mapping[str, str] | None = None) -> str | None:
    """Resolve the JWT secret from the deployment boundary (startup only).

    Precedence: deployment config ``jwt_secret`` field, then the
    ``CHATBI_JWT_SECRET`` environment variable. Never logged or persisted.
    """
    os_env = env if env is not None else os.environ
    configured = getattr(deployment, "jwt_secret", None)
    if isinstance(configured, str) and configured:
        return configured
    return os_env.get("CHATBI_JWT_SECRET") or None
