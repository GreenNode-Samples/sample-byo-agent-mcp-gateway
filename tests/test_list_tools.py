import httpx
import pytest

import list_tools


def test_parse_args_defaults_to_env(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_URL", "https://gw.example/stock")
    ns = list_tools.parse_args([])
    assert ns.url == "https://gw.example/stock"
    assert ns.call is None and ns.arguments == {}


def test_parse_args_call_with_json():
    ns = list_tools.parse_args(
        ["--url", "https://x/y", "--call", "stock__stock_quote", "--args", '{"symbol":"VNM"}']
    )
    assert ns.call == "stock__stock_quote"
    assert ns.arguments == {"symbol": "VNM"}


@pytest.mark.parametrize("argv", [[], ["--url", "u", "--args", "{bad"], ["--url", "u", "--args", "[1]"]])
def test_parse_args_errors(argv):
    with pytest.raises(SystemExit):
        list_tools.parse_args(argv)


def test_http_error_messages():
    assert "Inbound Auth" in list_tools.describe_http_error(401)
    assert "Policy Group" in list_tools.describe_http_error(403)
    assert "connector" in list_tools.describe_http_error(404)
    assert "gateway" in list_tools.describe_http_error(502)


def _status_error(code):
    req = httpx.Request("POST", "https://gw/x")
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(code, request=req))


def test_find_status_direct_nested_and_group():
    assert list_tools.find_http_status(_status_error(403)) == 403
    assert list_tools.find_http_status(ExceptionGroup("g", [ValueError(), ExceptionGroup("h", [_status_error(401)])])) == 401
    assert list_tools.find_http_status(ValueError("x")) is None


@pytest.mark.parametrize("code,needle", [(401, "Inbound Auth"), (403, "Policy Group")])
def test_main_maps_errors(monkeypatch, capsys, code, needle):
    async def boom(*a, **k):
        raise ExceptionGroup("unhandled errors in a TaskGroup", [_status_error(code)])

    monkeypatch.setattr(list_tools, "run", boom)
    assert list_tools.main(["--url", "https://gw/x"]) == 1
    assert needle in capsys.readouterr().err


def test_main_config_error(monkeypatch, capsys):
    async def boom(*a, **k):
        raise list_tools.AuthConfigError("thiếu credential")

    monkeypatch.setattr(list_tools, "run", boom)
    assert list_tools.main(["--url", "https://gw/x"]) == 1
    assert "thiếu credential" in capsys.readouterr().err


def test_mcp_session_terminated_maps_to_404():
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    err = McpError(ErrorData(code=32600, message="Session terminated"))
    assert list_tools.find_http_status(ExceptionGroup("g", [err])) == 404


def test_main_network_error(monkeypatch, capsys):
    async def boom(*a, **k):
        raise ExceptionGroup("g", [httpx.ConnectError("refused")])

    monkeypatch.setattr(list_tools, "run", boom)
    assert list_tools.main(["--url", "https://gw/x"]) == 1
    assert "Private" in capsys.readouterr().err
