import asyncio

import httpx
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


def test_iam_failures_are_reported_as_iam_problems(monkeypatch, creds):
    req = httpx.Request("POST", gateway_auth.IAM_TOKEN_URL)

    def bad_secret(url, **kw):
        return httpx.Response(401, request=req, json={"error": "invalid_client"})

    monkeypatch.setattr(gateway_auth.httpx, "post", bad_secret)
    with pytest.raises(gateway_auth.AuthConfigError, match=r"IAM token request failed: HTTP 401 from IAM"):
        gateway_auth.get_iam_token()


def test_iam_network_error(monkeypatch, creds):
    def down(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(gateway_auth.httpx, "post", down)
    with pytest.raises(gateway_auth.AuthConfigError, match=r"IAM token request failed: ConnectError"):
        gateway_auth.get_iam_token()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"token": "x"}),
        httpx.Response(200, json=["x"]),
        httpx.Response(200, json={"access_token": None}),
        httpx.Response(200, json={"access_token": ""}),
    ],
)
def test_iam_malformed_response(monkeypatch, creds, response):
    response.request = httpx.Request("POST", gateway_auth.IAM_TOKEN_URL)
    monkeypatch.setattr(gateway_auth.httpx, "post", lambda url, **kw: response)
    with pytest.raises(gateway_auth.AuthConfigError, match=r"IAM token request failed: malformed response"):
        gateway_auth.get_iam_token()


def test_failed_token_request_is_not_cached(monkeypatch, creds):
    monkeypatch.setattr(gateway_auth.httpx, "post", lambda url, **kw: httpx.Response(
        500, request=httpx.Request("POST", url)))
    with pytest.raises(gateway_auth.AuthConfigError):
        gateway_auth.get_iam_token()
    patch_post(monkeypatch, ["tok"])
    assert gateway_auth.get_iam_token() == "tok"


@pytest.mark.parametrize("value", ["", "  ", "change-me"])
def test_placeholder_or_empty_credentials_are_missing(monkeypatch, value):
    monkeypatch.setenv("GREENNODE_CLIENT_ID", value)
    monkeypatch.setenv("GREENNODE_CLIENT_SECRET", "sec")
    with pytest.raises(gateway_auth.AuthConfigError, match="GREENNODE_CLIENT_ID"):
        gateway_auth.get_iam_token()


def test_placeholder_jwt_is_rejected(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH", "jwt")
    monkeypatch.setenv("GATEWAY_JWT", "change-me")
    with pytest.raises(gateway_auth.AuthConfigError):
        gateway_auth.auth_headers()


def test_validate_auth_config(monkeypatch, tmp_path):
    monkeypatch.setenv("GATEWAY_AUTH", "none")
    gateway_auth.validate_auth_config()
    monkeypatch.setenv("GATEWAY_AUTH", "iam")
    with pytest.raises(gateway_auth.AuthConfigError, match="GREENNODE_CLIENT_ID"):
        gateway_auth.validate_auth_config()
    monkeypatch.setenv("GREENNODE_CLIENT_ID", "cid")
    monkeypatch.setenv("GREENNODE_CLIENT_SECRET", "sec")
    gateway_auth.validate_auth_config()
    monkeypatch.setenv("GATEWAY_AUTH", "jwt")
    with pytest.raises(gateway_auth.AuthConfigError, match="GATEWAY_JWT"):
        gateway_auth.validate_auth_config()
    monkeypatch.setenv("GATEWAY_JWT_FILE", str(tmp_path / "missing"))
    with pytest.raises(gateway_auth.AuthConfigError, match="GATEWAY_JWT_FILE"):
        gateway_auth.validate_auth_config()


# --- GatewayAuth: per-request credentials for a long-lived HTTP client ------------------------------------


def call_through_auth(server, n=1):
    """Send `n` requests through an httpx client that uses GatewayAuth; return the Authorization header of each attempt."""
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return server(len(seen), request)

    async def go():
        async with httpx.AsyncClient(auth=gateway_auth.GatewayAuth(), transport=httpx.MockTransport(handler)) as http:
            return [(await http.post("https://gw/x", json={})).status_code for _ in range(n)]

    return asyncio.run(go()), seen


def test_gateway_auth_uses_the_current_token_for_every_request(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH", "jwt")
    monkeypatch.setenv("GATEWAY_JWT", "first")

    async def go():
        async with httpx.AsyncClient(auth=gateway_auth.GatewayAuth(), transport=httpx.MockTransport(
                lambda r: httpx.Response(200, headers={"seen": r.headers["authorization"]}))) as http:
            first = await http.get("https://gw/x")
            monkeypatch.setenv("GATEWAY_JWT", "second")  # rotated while the client stays open
            second = await http.get("https://gw/x")
            return first.headers["seen"], second.headers["seen"]

    assert asyncio.run(go()) == ("Bearer first", "Bearer second")


def test_gateway_auth_refreshes_an_expired_iam_token_mid_session(monkeypatch, creds):
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(gateway_auth.time, "time", lambda: clock["t"])
    t1, t2 = make_jwt(clock["t"] + 1800), make_jwt(clock["t"] + 3600)
    calls = patch_post(monkeypatch, [t1, t2])

    async def go():
        seen = []
        async with httpx.AsyncClient(auth=gateway_auth.GatewayAuth(), transport=httpx.MockTransport(
                lambda r: seen.append(r.headers["authorization"]) or httpx.Response(200))) as http:
            await http.get("https://gw/x")
            clock["t"] += 1800  # the token has expired
            await http.get("https://gw/x")
        return seen

    assert asyncio.run(go()) == [f"Bearer {t1}", f"Bearer {t2}"]
    assert len(calls) == 2


def test_gateway_auth_retries_once_with_a_fresh_iam_token_on_401(monkeypatch, creds):
    patch_post(monkeypatch, ["stale", "fresh", "never-used"])
    statuses, seen = call_through_auth(lambda n, r: httpx.Response(200 if n == 2 else 401))
    assert statuses == [200]
    assert seen == ["Bearer stale", "Bearer fresh"]


def test_gateway_auth_gives_up_after_one_retry(monkeypatch, creds):
    patch_post(monkeypatch, ["a", "b", "c"])
    statuses, seen = call_through_auth(lambda n, r: httpx.Response(401))
    assert statuses == [401] and seen == ["Bearer a", "Bearer b"]


def test_gateway_auth_does_not_retry_when_the_credential_did_not_change(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH", "jwt")
    monkeypatch.setenv("GATEWAY_JWT", "same")
    statuses, seen = call_through_auth(lambda n, r: httpx.Response(401))
    assert statuses == [401] and seen == ["Bearer same"]


def test_gateway_auth_picks_up_a_rotated_jwt_file_on_401(monkeypatch, tmp_path):
    token_file = tmp_path / "t.jwt"
    token_file.write_text("old")
    monkeypatch.setenv("GATEWAY_AUTH", "jwt")
    monkeypatch.setenv("GATEWAY_JWT_FILE", str(token_file))

    def server(n, request):
        token_file.write_text("new")  # a refresh script rotated the file after the first request went out
        return httpx.Response(200 if n == 2 else 401)

    statuses, seen = call_through_auth(server)
    assert statuses == [200] and seen == ["Bearer old", "Bearer new"]


def test_gateway_auth_sends_no_header_in_none_mode(monkeypatch):
    monkeypatch.setenv("GATEWAY_AUTH", "none")
    statuses, seen = call_through_auth(lambda n, r: httpx.Response(401))
    assert statuses == [401] and seen == [None]
