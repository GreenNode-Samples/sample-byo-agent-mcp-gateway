import httpx
import pytest

import gateway_auth
import list_tools

TOOL_ARGS = ["--args", '{"symbol":"VNM"}']


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


def test_list_tools(stub, capsys):
    assert list_tools.main([]) == 0
    out = capsys.readouterr().out
    assert "4 tool(s)" in out and "- stock_quote: Latest price of a stock." in out


def test_call_tool(stub, capsys):
    assert list_tools.main(["--call", "stock_quote", *TOOL_ARGS]) == 0
    assert '"price": 61000' in capsys.readouterr().out


def test_failing_tool_exits_2_without_traceback(stub, capsys):
    assert list_tools.main(["--call", "fail_tool", *TOOL_ARGS]) == 2
    captured = capsys.readouterr()
    assert "upstream broke" in captured.out
    assert "isError=true" in captured.err and "Traceback" not in captured.err


def test_unknown_tool_exits_2_without_traceback(stub, capsys):
    assert list_tools.main(["--call", "no_such_tool"]) == 2
    captured = capsys.readouterr()
    assert "no_such_tool" in captured.out + captured.err and "Traceback" not in captured.err


def test_policy_denied_tool_exits_1_with_the_policy_hint(stub, capsys):
    assert list_tools.main(["--call", "denied_tool", *TOOL_ARGS]) == 1
    assert "Policy Group" in capsys.readouterr().err


def test_rejected_credential_exits_1_with_the_inbound_auth_hint(stub, capsys):
    stub.valid_tokens = {"some-other-token"}
    assert list_tools.main([]) == 1
    assert "Inbound Auth" in capsys.readouterr().err


def test_iam_failure_is_not_reported_as_a_gateway_problem(monkeypatch, capsys):
    monkeypatch.setenv("GATEWAY_AUTH", "iam")
    monkeypatch.setenv("GREENNODE_CLIENT_ID", "cid")
    monkeypatch.setenv("GREENNODE_CLIENT_SECRET", "wrong")
    monkeypatch.setattr(gateway_auth.httpx, "post", lambda url, **kw: httpx.Response(401, request=httpx.Request("POST", url)))
    assert list_tools.main(["--url", "https://gw.example/stock"]) == 1
    err = capsys.readouterr().err
    assert "Configuration error: IAM token request failed" in err
    assert "Inbound Auth" not in err


@pytest.mark.parametrize(
    "url,needle",
    [("https://gw-<gateway>-<id>.example/stock", "not a valid http"), ("gw.example/stock", "not a valid http")],
)
def test_main_rejects_invalid_url_before_any_network_call(monkeypatch, capsys, url, needle):
    monkeypatch.setenv("GATEWAY_AUTH", "none")
    assert list_tools.main(["--url", url]) == 1
    assert needle in capsys.readouterr().err


def test_main_requires_credentials_before_any_network_call(monkeypatch, capsys):
    assert list_tools.main(["--url", "https://gw.example/stock"]) == 1  # GATEWAY_AUTH defaults to iam
    assert "GREENNODE_CLIENT_ID" in capsys.readouterr().err


def test_main_reports_unreachable_gateway(monkeypatch, capsys):
    monkeypatch.setenv("GATEWAY_AUTH", "none")
    assert list_tools.main(["--url", "http://127.0.0.1:9/stock"]) == 1  # nothing listens on the discard port
    assert "Network error" in capsys.readouterr().err
