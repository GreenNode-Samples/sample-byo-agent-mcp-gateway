"""Obtain credentials for calling the MCP Gateway from OUTSIDE AgentBase.

The mode (env `GATEWAY_AUTH`) must match the **Inbound Auth** configured on the gateway:

- ``iam`` (default): Bearer IAM token obtained via the client-credentials grant of a GreenNode
  *service account* (`GREENNODE_CLIENT_ID` / `GREENNODE_CLIENT_SECRET`).
- ``jwt``: Bearer JWT issued by YOUR IdP (Okta, Auth0, Keycloak, Entra ID...).
  Read from env `GATEWAY_JWT` or file `GATEWAY_JWT_FILE` (re-read on every call,
  so it works with a background token-refresh script).
- ``none``: no header is sent (dev only, when the gateway uses *No authorization*).
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time

import httpx

IAM_TOKEN_URL = "https://iam.api.vngcloud.vn/accounts-api/v2/auth/token"
EXPIRY_MARGIN_SECONDS = 60  # refresh the token 60s before it expires
DEFAULT_TTL_SECONDS = 25 * 60  # used when the token has no `exp` claim

_lock = threading.Lock()
_cache: dict = {"token": None, "exp": 0.0}


class AuthConfigError(RuntimeError):
    """Credential configuration is missing or invalid."""


def _jwt_exp(token: str) -> float:
    """Read the JWT `exp` claim (signature is not verified). Returns 0.0 if it cannot be read."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return float(json.loads(base64.urlsafe_b64decode(part)).get("exp", 0))
    except Exception:
        return 0.0


def get_iam_token(force: bool = False) -> str:
    """IAM access token (client-credentials), cached and thread-safe, with a 60s margin."""
    with _lock:
        now = time.time()
        if not force and _cache["token"] and now < _cache["exp"] - EXPIRY_MARGIN_SECONDS:
            return _cache["token"]

        client_id = os.environ.get("GREENNODE_CLIENT_ID")
        client_secret = os.environ.get("GREENNODE_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise AuthConfigError(
                "Missing GREENNODE_CLIENT_ID / GREENNODE_CLIENT_SECRET. Outside AgentBase these variables "
                "are NOT injected automatically — create an IAM service account and put them in .env (see README)."
            )
        resp = httpx.post(
            IAM_TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        _cache["token"] = token
        _cache["exp"] = _jwt_exp(token) or (now + DEFAULT_TTL_SECONDS)
        return token


def clear_cache() -> None:
    with _lock:
        _cache["token"] = None
        _cache["exp"] = 0.0


def get_jwt() -> str:
    """JWT from your IdP: env GATEWAY_JWT or file GATEWAY_JWT_FILE."""
    token = os.environ.get("GATEWAY_JWT", "").strip()
    path = os.environ.get("GATEWAY_JWT_FILE", "").strip()
    if not token and path:
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                token = f.read().strip()
        except OSError as e:
            raise AuthConfigError(f"Could not read GATEWAY_JWT_FILE={path}: {e}") from e
    if not token:
        raise AuthConfigError("GATEWAY_AUTH=jwt but GATEWAY_JWT or GATEWAY_JWT_FILE is missing.")
    return token


def auth_mode() -> str:
    mode = os.environ.get("GATEWAY_AUTH", "iam").strip().lower()
    if mode not in ("iam", "jwt", "none"):
        raise AuthConfigError(f"GATEWAY_AUTH='{mode}' is invalid (choose: iam | jwt | none).")
    return mode


def auth_headers() -> dict[str, str]:
    """Authorization header according to GATEWAY_AUTH (iam | jwt | none)."""
    mode = auth_mode()
    if mode == "none":
        return {}
    token = get_iam_token() if mode == "iam" else get_jwt()
    return {"Authorization": f"Bearer {token}"}
