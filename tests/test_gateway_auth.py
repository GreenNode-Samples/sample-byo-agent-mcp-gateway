import pytest

import gateway_auth
from conftest import make_jwt


class FakeResp:
    def __init__(self, token):
        self._token = token

    def raise_for_status(self):
        pass

    def json(self):
        return {"access_token": self._token}


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("GREENNODE_CLIENT_ID", "cid")
    monkeypatch.setenv("GREENNODE_CLIENT_SECRET", "sec")


def patch_post(monkeypatch, tokens):
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        return FakeResp(tokens[min(len(calls) - 1, len(tokens) - 1)])

    monkeypatch.setattr(gateway_auth.httpx, "post", fake_post)
    return calls


def test_token_request_shape_and_cache(monkeypatch, creds):
    now = 1_000_000.0
    monkeypatch.setattr(gateway_auth.time, "time", lambda: now)
    calls = patch_post(monkeypatch, [make_jwt(now + 1800)])
    assert gateway_auth.get_iam_token() == gateway_auth.get_iam_token()
    assert len(calls) == 1
    url, kw = calls[0]
    assert url == gateway_auth.IAM_TOKEN_URL
    assert kw["auth"] == ("cid", "sec")
    assert kw["data"] == {"grant_type": "client_credentials"}


def test_refresh_inside_60s_margin(monkeypatch, creds):
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(gateway_auth.time, "time", lambda: clock["t"])
    t1, t2 = make_jwt(clock["t"] + 1800), make_jwt(clock["t"] + 3600)
    calls = patch_post(monkeypatch, [t1, t2])
    assert gateway_auth.get_iam_token() == t1
    clock["t"] += 1800 - 61  # 61s left -> still served from cache
    assert gateway_auth.get_iam_token() == t1
    clock["t"] += 2  # 59s left -> refreshed
    assert gateway_auth.get_iam_token() == t2
    assert len(calls) == 2


def test_force_refresh(monkeypatch, creds):
    now = 1_000_000.0
    monkeypatch.setattr(gateway_auth.time, "time", lambda: now)
    calls = patch_post(monkeypatch, [make_jwt(now + 1800), make_jwt(now + 1800) + "x"])
    gateway_auth.get_iam_token()
    gateway_auth.get_iam_token(force=True)
    assert len(calls) == 2


def test_token_without_exp_uses_default_ttl(monkeypatch, creds):
    now = 1_000_000.0
    monkeypatch.setattr(gateway_auth.time, "time", lambda: now)
    calls = patch_post(monkeypatch, ["opaque-token"])
    gateway_auth.get_iam_token()
    now += gateway_auth.DEFAULT_TTL_SECONDS - 61
    gateway_auth.get_iam_token()
    assert len(calls) == 1


def test_missing_credentials(monkeypatch):
    patch_post(monkeypatch, ["x"])
    with pytest.raises(gateway_auth.AuthConfigError, match="GREENNODE_CLIENT_ID"):
        gateway_auth.get_iam_token()


def test_auth_headers_default_is_iam(monkeypatch, creds):
    patch_post(monkeypatch, ["tok"])
    assert gateway_auth.auth_headers() == {"Authorization": "Bearer tok"}


def test_auth_headers_jwt_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH", "jwt")
    monkeypatch.setenv("GATEWAY_JWT", " my.jwt.token ")
    assert gateway_auth.auth_headers() == {"Authorization": "Bearer my.jwt.token"}


def test_auth_headers_jwt_from_file(monkeypatch, tmp_path):
    f = tmp_path / "t.jwt"
    f.write_text("file.jwt.token\n")
    monkeypatch.setenv("GATEWAY_AUTH", "jwt")
    monkeypatch.setenv("GATEWAY_JWT_FILE", str(f))
    assert gateway_auth.auth_headers() == {"Authorization": "Bearer file.jwt.token"}
    f.write_text("rotated\n")  # re-read on every call
    assert gateway_auth.auth_headers() == {"Authorization": "Bearer rotated"}


def test_jwt_missing_or_unreadable(monkeypatch, tmp_path):
    monkeypatch.setenv("GATEWAY_AUTH", "jwt")
    with pytest.raises(gateway_auth.AuthConfigError):
        gateway_auth.auth_headers()
    monkeypatch.setenv("GATEWAY_JWT_FILE", str(tmp_path / "nope"))
    with pytest.raises(gateway_auth.AuthConfigError, match="GATEWAY_JWT_FILE"):
        gateway_auth.auth_headers()


def test_auth_none(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH", "none")
    assert gateway_auth.auth_headers() == {}


def test_invalid_mode(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH", "basic")
    with pytest.raises(gateway_auth.AuthConfigError):
        gateway_auth.auth_headers()
