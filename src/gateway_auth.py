"""Lấy credential để gọi MCP Gateway từ BÊN NGOÀI AgentBase.

Chế độ (env `GATEWAY_AUTH`) phải khớp với **Inbound Auth** cấu hình trên gateway:

- ``iam`` (mặc định): Bearer token IAM lấy bằng client-credentials của một
  *service account* GreenNode (`GREENNODE_CLIENT_ID` / `GREENNODE_CLIENT_SECRET`).
- ``jwt``: Bearer JWT do IdP của BẠN cấp (Okta, Auth0, Keycloak, Entra ID...).
  Đọc từ env `GATEWAY_JWT` hoặc file `GATEWAY_JWT_FILE` (đọc lại mỗi lần gọi,
  nên có thể dùng với script refresh token chạy nền).
- ``none``: không gửi header (chỉ dùng cho dev khi gateway chọn *No authorization*).
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time

import httpx

IAM_TOKEN_URL = "https://iam.api.vngcloud.vn/accounts-api/v2/auth/token"
EXPIRY_MARGIN_SECONDS = 60  # làm mới token sớm 60s trước khi hết hạn
DEFAULT_TTL_SECONDS = 25 * 60  # dùng khi token không có claim `exp`

_lock = threading.Lock()
_cache: dict = {"token": None, "exp": 0.0}


class AuthConfigError(RuntimeError):
    """Cấu hình credential thiếu hoặc sai."""


def _jwt_exp(token: str) -> float:
    """Đọc claim `exp` của JWT (không verify chữ ký). 0.0 nếu không đọc được."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return float(json.loads(base64.urlsafe_b64decode(part)).get("exp", 0))
    except Exception:
        return 0.0


def get_iam_token(force: bool = False) -> str:
    """IAM access token (client-credentials), cache + thread-safe, margin 60s."""
    with _lock:
        now = time.time()
        if not force and _cache["token"] and now < _cache["exp"] - EXPIRY_MARGIN_SECONDS:
            return _cache["token"]

        client_id = os.environ.get("GREENNODE_CLIENT_ID")
        client_secret = os.environ.get("GREENNODE_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise AuthConfigError(
                "Thiếu GREENNODE_CLIENT_ID / GREENNODE_CLIENT_SECRET. Ngoài AgentBase các biến này "
                "KHÔNG được tự inject — hãy tạo IAM service account và đặt vào .env (xem README)."
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
    """JWT từ IdP của bạn: env GATEWAY_JWT hoặc file GATEWAY_JWT_FILE."""
    token = os.environ.get("GATEWAY_JWT", "").strip()
    path = os.environ.get("GATEWAY_JWT_FILE", "").strip()
    if not token and path:
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                token = f.read().strip()
        except OSError as e:
            raise AuthConfigError(f"Không đọc được GATEWAY_JWT_FILE={path}: {e}") from e
    if not token:
        raise AuthConfigError("GATEWAY_AUTH=jwt nhưng thiếu GATEWAY_JWT hoặc GATEWAY_JWT_FILE.")
    return token


def auth_mode() -> str:
    mode = os.environ.get("GATEWAY_AUTH", "iam").strip().lower()
    if mode not in ("iam", "jwt", "none"):
        raise AuthConfigError(f"GATEWAY_AUTH='{mode}' không hợp lệ (chọn: iam | jwt | none).")
    return mode


def auth_headers() -> dict[str, str]:
    """Header Authorization theo GATEWAY_AUTH (iam | jwt | none)."""
    mode = auth_mode()
    if mode == "none":
        return {}
    token = get_iam_token() if mode == "iam" else get_jwt()
    return {"Authorization": f"Bearer {token}"}
