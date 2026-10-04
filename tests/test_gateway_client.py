import asyncio

import httpx
import pytest
from langchain_core.tools import StructuredTool
from mcp.types import CallToolResult, ImageContent, TextContent

import gateway_client as client
from gateway_errors import ConfigError


def rpc(method="tools/call", rpc_id=7):
    return httpx.Request("POST", "https://gw/stock", json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": {}})


def send(handler, request):
    async def go():
        async with client.GatewayHttpClient(transport=httpx.MockTransport(handler)) as http:
            return await http.send(request)

    return asyncio.run(go())


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
def test_failed_request_becomes_a_json_rpc_error(status):
    response = send(lambda request: httpx.Response(status, text="no"), rpc())
    body = response.json()
    assert response.status_code == 200
    assert body["id"] == 7 and body["jsonrpc"] == "2.0"
    assert body["error"]["data"] == {"http_status": status}


@pytest.mark.parametrize(
    "exc,kind",
    [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectTimeout("slow"), "timeout"), (httpx.ConnectError("refused"), "network")],
)
def test_transport_errors_become_json_rpc_errors(exc, kind):
    def handler(request):
        raise exc

    body = send(handler, rpc("initialize", "abc")).json()
    assert body["id"] == "abc" and body["error"]["data"] == {"network_error": kind}


def test_success_and_redirects_pass_through():
    assert send(lambda r: httpx.Response(200, json={"ok": 1}), rpc()).json() == {"ok": 1}
    redirect = send(lambda r: httpx.Response(307, headers={"location": "https://gw/stock/"}), rpc())
    assert redirect.status_code == 307  # the SDK follows redirects itself


def test_requests_without_an_id_are_left_alone():
    """Notifications, the GET stream and DELETE are not JSON-RPC requests: errors there keep their usual handling."""
    notification = httpx.Request("POST", "https://gw/stock", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert send(lambda r: httpx.Response(403), notification).status_code == 403
    assert send(lambda r: httpx.Response(405), httpx.Request("GET", "https://gw/stock")).status_code == 405

    def boom(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        send(boom, httpx.Request("DELETE", "https://gw/stock"))


def test_server_name():
    assert client.server_name("https://gw-a-1.example.vn/stock") == "stock"
    assert client.server_name("https://gw.example/hotel/") == "hotel"
    assert client.server_name("https://gw.example") == "gw.example"


def tool(name):
    async def noop() -> str:
        return ""

    return StructuredTool(name=name, description=name, coroutine=noop, args_schema={"type": "object", "properties": {}})


def test_ensure_unique_tool_names():
    client.ensure_unique_tool_names({"stock": [tool("quote")], "hotel": [tool("book")]})
    with pytest.raises(ConfigError, match="'quote' is exposed by connector 'stock' and by 'hotel'"):
        client.ensure_unique_tool_names({"stock": [tool("quote")], "hotel": [tool("quote")]})


def test_truncate_tool_output():
    limit = client.MAX_TOOL_OUTPUT_CHARS
    image = ImageContent(type="image", data="aGk=", mimeType="image/png")
    result = CallToolResult(content=[TextContent(type="text", text="a" * (limit + 5)), TextContent(type="text", text="short"), image])

    async def handler(request):
        return result

    out = asyncio.run(client.truncate_tool_output(None, handler))
    long, short, kept = out.content
    assert long.text == "a" * limit + "\n[... truncated 5 characters ...]"
    assert short.text == "short" and kept is image
    assert len(result.content[0].text) == limit + 5  # the original result is not modified


def test_truncate_tool_output_leaves_other_results_alone():
    async def handler(request):
        return "a tool message"

    assert asyncio.run(client.truncate_tool_output(None, handler)) == "a tool message"
    ok = CallToolResult(content=[TextContent(type="text", text="fine")])

    async def ok_handler(request):
        return ok

    assert asyncio.run(client.truncate_tool_output(None, ok_handler)).content[0].text == "fine"
