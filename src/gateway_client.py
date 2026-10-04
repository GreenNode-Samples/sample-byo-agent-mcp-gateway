"""Connect to the MCP Gateway connectors and expose their tools to LangChain.

One long-lived MCP session per connector serves the whole agent run (instead of a new session for every
tool call). Two things make that safe:

- `GatewayAuth` (see `gateway_auth.py`) adds `Authorization` to every HTTP request, so the session keeps
  working after the IAM token expires and is refreshed.
- `GatewayHttpClient` turns a failed HTTP request (401/403/404/5xx, timeout) into a JSON-RPC error. The `mcp`
  SDK would otherwise raise it inside the transport's task group, which closes the whole session: one denied
  tool would break every following call.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from urllib.parse import urlparse

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.types import CallToolResult, TextContent

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gateway_auth import GatewayAuth  # noqa: E402
from gateway_errors import HTTP_STATUS_KEY, NETWORK_ERROR_KEY, ConfigError  # noqa: E402

HTTP_TIMEOUT_SECONDS = 30.0  # connect / write / pool
READ_TIMEOUT_SECONDS = 120.0  # wait for the gateway's response (a tool call may stream its answer)
MAX_TOOL_OUTPUT_CHARS = 8_000  # longer tool results are cut before they reach the model
JSONRPC_SERVER_ERROR = -32000


def _jsonrpc_request_id(request: httpx.Request) -> int | str | None:
    """Id of the JSON-RPC *request* carried by an MCP POST (None for notifications, GET and DELETE)."""
    if request.method != "POST":
        return None
    try:
        body = json.loads(request.read())  # read(): a request rebuilt for a redirect has an unread body stream
    except ValueError:
        return None
    return body.get("id") if isinstance(body, dict) and "method" in body else None


class GatewayHttpClient(httpx.AsyncClient):
    """`httpx.AsyncClient` that reports failed MCP requests as JSON-RPC errors instead of raising.

    The error carries `data={"http_status": ...}` or `data={"network_error": "timeout" | "network"}`, which
    `gateway_errors.find_http_status` / `find_network_error` understand.
    """

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        request_id = _jsonrpc_request_id(request)
        try:
            response = await super().send(request, **kwargs)
        except httpx.TransportError as e:
            if request_id is None:
                raise
            kind = "timeout" if isinstance(e, httpx.TimeoutException) else "network"
            detail = f"{type(e).__name__}: {e}".rstrip(": ")
            return self._jsonrpc_error(request, request_id, detail, {NETWORK_ERROR_KEY: kind})
        if request_id is None or not response.is_error:  # redirects are followed by the SDK itself
            return response
        await response.aclose()
        detail = f"HTTP {response.status_code} {response.reason_phrase}"
        return self._jsonrpc_error(request, request_id, detail, {HTTP_STATUS_KEY: response.status_code})

    @staticmethod
    def _jsonrpc_error(request: httpx.Request, request_id: int | str, message: str, data: dict) -> httpx.Response:
        error = {"code": JSONRPC_SERVER_ERROR, "message": message, "data": data}
        payload = {"jsonrpc": "2.0", "id": request_id, "error": error}
        return httpx.Response(200, json=payload, request=request)


def gateway_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """`httpx_client_factory` for langchain-mcp-adapters connections."""
    # follow_redirects: mcp < 1.30 relies on the client for redirects; newer versions follow them within the origin themselves
    return GatewayHttpClient(headers=headers, timeout=timeout, auth=auth, follow_redirects=True)


def server_name(url: str) -> str:
    """Name a connector by the last segment of its URL path (`…/stock` -> `stock`)."""
    parsed = urlparse(url)
    return parsed.path.rstrip("/").rsplit("/", 1)[-1] or parsed.netloc


def build_connections(urls: list[str]) -> dict:
    """One streamable-HTTP connection per connector URL, named by connector path."""
    connections: dict = {}
    auth = GatewayAuth()
    for url in urls:
        name = server_name(url)
        if name in connections:
            raise ConfigError(
                f"Two connector URLs share the name '{name}' ({connections[name]['url']} and {url}). "
                "Use each connector URL once."
            )
        connections[name] = {
            "transport": "streamable_http",
            "url": url,
            "auth": auth,
            "httpx_client_factory": gateway_http_client,
            "timeout": HTTP_TIMEOUT_SECONDS,
            "sse_read_timeout": READ_TIMEOUT_SECONDS,
        }
    return connections


async def truncate_tool_output(request: MCPToolCallRequest, handler):
    """Tool-call interceptor: cut oversized text results so one call cannot flood the model's context."""
    result = await handler(request)
    if not isinstance(result, CallToolResult):
        return result
    content = []
    for block in result.content:
        if isinstance(block, TextContent) and len(block.text) > MAX_TOOL_OUTPUT_CHARS:
            omitted = len(block.text) - MAX_TOOL_OUTPUT_CHARS
            marker = f"\n[... truncated {omitted} characters ...]"
            block = block.model_copy(update={"text": block.text[:MAX_TOOL_OUTPUT_CHARS] + marker})
        content.append(block)
    return result.model_copy(update={"content": content})


def ensure_unique_tool_names(tools_by_server: dict[str, list[BaseTool]]) -> None:
    """Fail loudly if two connectors expose the same tool name (the model could not tell them apart)."""
    owners: dict[str, str] = {}
    for server, tools in tools_by_server.items():
        for tool in tools:
            if tool.name in owners:
                raise ConfigError(
                    f"Tool '{tool.name}' is exposed by connector '{owners[tool.name]}' and by '{server}'. "
                    "Tool names must be unique across MCP_GATEWAY_URLS: drop one of the connectors or "
                    "rename the tool on its MCP server."
                )
            owners[tool.name] = server


async def load_gateway_tools(stack: AsyncExitStack, urls: list[str]) -> list[BaseTool]:
    """Open one MCP session per connector (closed when `stack` closes) and return all their tools."""
    client = MultiServerMCPClient(build_connections(urls))
    tools_by_server: dict[str, list[BaseTool]] = {}
    for name in client.connections:
        session = await stack.enter_async_context(client.session(name))
        tools_by_server[name] = await load_mcp_tools(
            session, server_name=name, tool_interceptors=[truncate_tool_output]
        )
    ensure_unique_tool_names(tools_by_server)
    return [tool for tools in tools_by_server.values() for tool in tools]
