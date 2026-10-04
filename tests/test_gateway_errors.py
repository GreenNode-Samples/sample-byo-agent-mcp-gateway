import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

import gateway_errors as errors

try:  # ExceptionGroup is built in from Python 3.11
    ExceptionGroup  # noqa: B018
except NameError:  # pragma: no cover
    from exceptiongroup import ExceptionGroup


def status_error(code):
    req = httpx.Request("POST", "https://gw/x")
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(code, request=req))


def rpc_error(**data):
    return McpError(ErrorData(code=-32000, message="gateway call failed", data=data))


def test_http_error_messages():
    assert "Inbound Auth" in errors.describe_http_error(401)
    assert "Policy Group" in errors.describe_http_error(403)
    assert "connector" in errors.describe_http_error(404)
    assert "gateway" in errors.describe_http_error(502)
    assert "unexpected" in errors.describe_http_error(418)


def test_find_status_direct_nested_and_group():
    assert errors.find_http_status(status_error(403)) == 403
    nested = ExceptionGroup("g", [ValueError(), ExceptionGroup("h", [status_error(401)])])
    assert errors.find_http_status(nested) == 401
    assert errors.find_http_status(ValueError("x")) is None


def test_mcp_session_terminated_maps_to_404():
    err = McpError(ErrorData(code=32600, message="Session terminated"))
    assert errors.find_http_status(ExceptionGroup("g", [err])) == 404


def test_status_and_network_errors_reported_as_json_rpc_errors():
    assert errors.find_http_status(rpc_error(http_status=403)) == 403
    assert errors.find_http_status(rpc_error(network_error="timeout")) is None
    assert errors.find_network_error(rpc_error(network_error="network")) is not None
    assert errors.is_timeout(rpc_error(network_error="timeout"))
    assert not errors.is_timeout(rpc_error(network_error="network"))
    assert not errors.is_timeout(rpc_error(http_status=503))
    assert errors.find_network_error(McpError(ErrorData(code=-32602, message="Unknown tool"))) is None


def test_network_errors():
    assert errors.find_network_error(ExceptionGroup("g", [httpx.ConnectError("refused")])) is not None
    assert errors.is_timeout(ExceptionGroup("g", [httpx.ReadTimeout("slow")]))
    assert not errors.is_timeout(httpx.ConnectError("refused"))
    assert errors.find_network_error(ValueError("x")) is None


def test_leaf_exception():
    leaf = ValueError("root")
    assert errors.leaf_exception(ExceptionGroup("g", [ExceptionGroup("h", [leaf])])) is leaf
    assert errors.leaf_exception(leaf) is leaf


def test_config_error_wins_over_the_http_error_it_was_caused_by():
    try:
        try:
            raise status_error(401)
        except httpx.HTTPStatusError as e:
            raise errors.ConfigError("IAM token request failed: HTTP 401 from IAM.") from e
    except errors.ConfigError as config_error:
        message = errors.describe_failure(ExceptionGroup("g", [config_error]))
    assert message.startswith("Configuration error: IAM token request failed")
    assert "Inbound Auth" not in message


@pytest.mark.parametrize(
    "exc,needle",
    [
        (status_error(403), "Policy Group"),
        (httpx.ConnectError("refused"), "Private gateway"),
    ],
)
def test_describe_failure(exc, needle):
    assert needle in errors.describe_failure(ExceptionGroup("g", [exc]))


def test_describe_failure_ignores_unknown_errors():
    assert errors.describe_failure(ValueError("bug")) is None


@pytest.mark.parametrize("url", ["https://gw.example/stock", "http://127.0.0.1:8000/x"])
def test_check_http_url_accepts(url):
    assert errors.check_http_url("U", url) == url


@pytest.mark.parametrize("url", ["", "gw.example/stock", "ftp://gw/x", "https://", "https://gw-<gateway>-<id>.example/<connector>"])
def test_check_http_url_rejects(url):
    with pytest.raises(errors.ConfigError, match="U="):
        errors.check_http_url("U", url)
