"""Obtain credentials for calling the MCP Gateway from OUTSIDE AgentBase.

The mode (env `GATEWAY_AUTH`) must match the **Inbound Auth** configured on the gateway:

- ``iam`` (default): Bearer IAM token obtained via the client-credentials grant of a GreenNode
  *service account* (`GREENNODE_CLIENT_ID` / `GREENNODE_CLIENT_SECRET`).
- ``jwt``: Bearer JWT issued by YOUR IdP (Okta, Auth0, Keycloak, Entra ID...).
  Read from env `GATEWAY_JWT` or file `GATEWAY_JWT_FILE` (re-read on every call,
  so it works with a background token-refresh script).
- ``none``: no header is sent (dev only, when the gateway uses *No authorization*).

`GatewayAuth` plugs these headers into an `httpx` client, per request.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time

import httpx

from gateway_errors import ConfigError

IAM_TOKEN_URL = "https://iam.api.vngcloud.vn/accounts-api/v2/auth/token"
EXPIRY_MARGIN_SECONDS = 60  # refresh the token 60s before it expires
DEFAULT_TTL_SECONDS = 25 * 60  # used when the token has no `exp` claim

PLACEHOLDER = "change-me"  # value used in .env.example; always rejected as "not configured"

_lock = threading.Lock()
_cache: dict = {"token": None, "exp": 0.0}


class AuthConfigError(ConfigError):
    """Credential configuration is missing or invalid, or the IAM token request failed."""


def is_unset(value: str | None) -> bool:
    """True for an empty value or the `.env.example` placeholder."""
    return not value or not value.strip() or value.strip() == PLACEHOLDER


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
        if is_unset(client_id) or is_unset(client_secret):
            raise AuthConfigError(
                "Missing GREENNODE_CLIENT_ID / GREENNODE_CLIENT_SECRET. Outside AgentBase these variables "
                "are NOT injected automatically — create an IAM service account and put them in .env (see README)."
            )
        token = _request_iam_token(client_id, client_secret)
        _cache["token"] = token
        _cache["exp"] = _jwt_exp(token) or (now + DEFAULT_TTL_SECONDS)
        return token


def _request_iam_token(client_id: str, client_secret: str) -> str:
    """Client-credentials grant against IAM. Every failure is reported as an IAM problem."""
    try:
        resp = httpx.post(
            IAM_TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
    except httpx.HTTPStatusError as e:
        raise AuthConfigError(
            f"IAM token request failed: HTTP {e.response.status_code} from IAM. "
            "Check GREENNODE_CLIENT_ID / GREENNODE_CLIENT_SECRET (service account, client-credentials grant)."
        ) from e
    except httpx.HTTPError as e:
        raise AuthConfigError(f"IAM token request failed: {type(e).__name__}: {e}") from e
    except (ValueError, KeyError, TypeError) as e:
        raise AuthConfigError("IAM token request failed: malformed response (no 'access_token').") from e
    if not isinstance(token, str) or not token:
        raise AuthConfigError("IAM token request failed: malformed response (empty 'access_token').")
    return token


def clear_cache() -> None:
    with _lock:
        _cache["token"] = None
        _cache["exp"] = 0.0


def get_jwt() -> str:
    """JWT from your IdP: env GATEWAY_JWT or file GATEWAY_JWT_FILE."""
    token = os.environ.get("GATEWAY_JWT", "").strip()
    path = os.environ.get("GATEWAY_JWT_FILE", "").strip()
    if is_unset(token):
        token = ""
    if not token and path:
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                token = f.read().strip()
        except OSError as e:
            raise AuthConfigError(f"Could not read GATEWAY_JWT_FILE={path}: {e}") from e
    if is_unset(token):
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


def validate_auth_config() -> None:
    """Fail fast (no network call) if the inputs required by `GATEWAY_AUTH` are missing or invalid."""
    mode = auth_mode()
    if mode == "iam" and (
        is_unset(os.environ.get("GREENNODE_CLIENT_ID")) or is_unset(os.environ.get("GREENNODE_CLIENT_SECRET"))
    ):
        raise AuthConfigError(
            "GATEWAY_AUTH=iam but GREENNODE_CLIENT_ID / GREENNODE_CLIENT_SECRET are missing. Outside AgentBase "
            "these variables are NOT injected automatically — create an IAM service account (see README)."
        )
    if mode == "jwt":
        get_jwt()


class GatewayAuth(httpx.Auth):
    """Attach `Authorization` to every HTTP request, so a long-lived MCP session survives token expiry.

    `auth_headers()` is called per request and serves the cached token (refreshed 60s before it expires).
    If the gateway still answers 401, the cache is cleared and the request is retried once with a fresh
    credential (only when the credential actually changed, e.g. a rotated IAM token or JWT file).
    A token refresh is a short blocking call, which is acceptable because it happens about twice an hour.
    """

    def auth_flow(self, request: httpx.Request):
        sent = auth_headers()
        request.headers.update(sent)
        response = yield request
        if response.status_code != 401:
            return
        clear_cache()
        fresh = auth_headers()
        if fresh != sent:
            request.headers.update(fresh)
            yield request
