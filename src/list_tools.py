"""CLI: list / call tools through the GreenNode AgentBase MCP Gateway.

    python src/list_tools.py                              # tools/list
    python src/list_tools.py --call stock_quote --args '{"symbol":"VNM"}'

Tool names are the FULL names exactly as the gateway returns them in tools/list (see the README on connector prefixes).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gateway_auth import AuthConfigError, auth_headers  # noqa: E402

HTTP_HINTS = {
    401: (
        "401 Unauthorized — the gateway's Inbound Auth rejected the credential. Check that GATEWAY_AUTH "
        "matches the mode configured on the gateway (IAM Permissions or JWT); that the token has not expired; "
        "and, for JWT, that the issuer/audience/JWKS match the gateway configuration."
    ),
    403: (
        "403 Forbidden — authentication succeeded, but the Policy Group denies this principal from calling "
        "this tool (first match wins; no matching rule => 403). Add an allow rule for your principal "
        "(iam:<service-account> or the configured JWT claim) with action '<connector>__<tool>'. "
        "Note: tools/list is not blocked by policy, only tools/call."
    ),
    404: "404 Not Found — wrong connector path in MCP_GATEWAY_URL (…/<connector>). Re-copy it from the gateway detail page.",
}


def describe_http_error(status: int) -> str:
    if status in HTTP_HINTS:
        return HTTP_HINTS[status]
    if status >= 500:
        return f"{status} — gateway/MCP server error. Retry later; if it persists, check the connector/runtime logs."
    return f"HTTP {status} — unexpected response from the gateway."


def _walk(exc: BaseException):
    """Walk an exception, including ExceptionGroup (anyio TaskGroup) members and __cause__."""
    yield exc
    for sub in getattr(exc, "exceptions", ()):
        yield from _walk(sub)
    if exc.__cause__ is not None:
        yield from _walk(exc.__cause__)


def find_http_status(exc: BaseException) -> int | None:
    """HTTP status that caused the error. The `mcp` SDK reports a 404 as McpError 'Session terminated'."""
    for e in _walk(exc):
        if isinstance(e, httpx.HTTPStatusError):
            return e.response.status_code
        if isinstance(e, McpError) and "session terminated" in str(e).lower():
            return 404
    return None


def find_network_error(exc: BaseException) -> BaseException | None:
    return next((e for e in _walk(exc) if isinstance(e, (httpx.ConnectError, httpx.TimeoutException))), None)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="List / call tools through the GreenNode MCP Gateway")
    p.add_argument("--url", default=os.environ.get("MCP_GATEWAY_URL"),
                   help="Connector URL on the gateway (default: env MCP_GATEWAY_URL)")
    p.add_argument("--call", metavar="TOOL", help="Call a single tool instead of just listing tools")
    p.add_argument("--args", default="{}", help="JSON arguments for --call, e.g. '{\"symbol\":\"VNM\"}'")
    ns = p.parse_args(argv)
    if not ns.url:
        p.error("missing URL: set MCP_GATEWAY_URL or pass --url")
    try:
        ns.arguments = json.loads(ns.args)
    except json.JSONDecodeError as e:
        p.error(f"--args is not valid JSON: {e}")
    if not isinstance(ns.arguments, dict):
        p.error("--args must be a JSON object")
    return ns


async def run(url: str, call: str | None, arguments: dict) -> None:
    async with streamablehttp_client(url, headers=auth_headers()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if call:
                result = await session.call_tool(call, arguments)
                for item in result.content:
                    print(getattr(item, "text", item))
                if result.isError:
                    print("\n[tool returned isError=true]", file=sys.stderr)
                    sys.exit(2)
                return
            tools = (await session.list_tools()).tools
            print(f"{len(tools)} tool(s) at {url}\n")
            for t in tools:
                desc = (t.description or "").strip().splitlines()
                print(f"- {t.name}: {desc[0] if desc else ''}")


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    try:
        asyncio.run(run(ns.url, ns.call, ns.arguments))
    except AuthConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        status = find_http_status(e)
        if status is not None:
            print(f"Error: {describe_http_error(status)}", file=sys.stderr)
        elif (net := find_network_error(e)) is not None:
            print(f"Network error: {net}. Check the URL; a Private gateway is only reachable from your private network.",
                  file=sys.stderr)
        else:
            raise
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
