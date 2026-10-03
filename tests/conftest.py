import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gateway_auth  # noqa: E402


def make_jwt(exp: float) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.sig"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("GATEWAY_AUTH", "GATEWAY_JWT", "GATEWAY_JWT_FILE", "GREENNODE_CLIENT_ID",
              "GREENNODE_CLIENT_SECRET", "MCP_GATEWAY_URL"):
        monkeypatch.delenv(k, raising=False)
    gateway_auth.clear_cache()
    yield
    gateway_auth.clear_cache()
