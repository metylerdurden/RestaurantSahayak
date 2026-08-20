"""Minimal API authorization boundary (Step 20 security hardening).

No user-account/session/login system exists in this MVP by design (see
`app.models.user.User`'s own docstring — auth was explicitly out of scope for the
Core Platform MVP spec) and Step 20 does not add one. What was genuinely missing:
*anything* stopping an unauthenticated caller from approving/rejecting a pending
Approval, cancelling a reservation, or triggering a workflow once this app is
reachable on a network it doesn't fully control — every Manager API route was
wide open.

`require_api_key` is a single shared secret (`Settings.api_key`, env var
`API_KEY`), checked via constant-time comparison. Left unset — the default for
local development and for the entire existing test suite — it is a no-op, so
nothing that already worked breaks. Set it, and every protected route requires a
matching `X-API-Key` header. This is deliberately not a "real" auth system (no
per-user identity, no session, no token issuance/expiry) — it is the minimum
boundary that turns "anyone on the network can approve a purchase" into "only
someone with the shared key can," which is the concrete risk Step 20 called out.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException

from app.core.config import Settings, get_settings


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.api_key:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
