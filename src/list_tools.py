"""CLI: list / call tools through the GreenNode AgentBase MCP Gateway.

    python src/list_tools.py                              # tools/list
    python src/list_tools.py --call stock__stock_quote --args '{"symbol":"VNM"}'

Tool names are the FULL names exactly as the gateway returns them in tools/list (see the README on connector prefixes).

Exit codes: 0 = success, 1 = configuration, authentication, network or gateway error,
2 = the tool failed or does not exist (also used by argparse for invalid usage).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gateway_auth import GatewayAuth, auth_headers, validate_auth_config  # noqa: E402
from gateway_errors import check_http_url, describe_failure, find_http_status  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_TOOL_FAILED = 0, 1, 2
HTTP_TIMEOUT = httpx.Timeout(30.0, read=120.0)


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


async def run(url: str, call: str | None, arguments: dict) -> int:
    """List tools, or call one. Returns the process exit code (never calls `sys.exit` inside the async context)."""
    auth_headers()  # fail fast, with a clean error, if the credential cannot be obtained
    async with (
        httpx.AsyncClient(auth=GatewayAuth(), timeout=HTTP_TIMEOUT, follow_redirects=True) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        if call:
            try:
                result = await session.call_tool(call, arguments)
            except McpError as e:
                if find_http_status(e) is not None:  # transport problem, reported by main()
                    raise
                print(f"Error: tool '{call}' failed: {e.error.message}", file=sys.stderr)
                return EXIT_TOOL_FAILED
            for item in result.content:
                print(getattr(item, "text", item))
            if result.isError:
                print(f"\n[tool '{call}' returned isError=true]", file=sys.stderr)
                return EXIT_TOOL_FAILED
            return EXIT_OK
        tools = (await session.list_tools()).tools
        print(f"{len(tools)} tool(s) at {url}\n")
        for t in tools:
            desc = (t.description or "").strip().splitlines()
            print(f"- {t.name}: {desc[0] if desc else ''}")
        return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    try:
        check_http_url("MCP_GATEWAY_URL / --url", ns.url)
        validate_auth_config()
        return asyncio.run(run(ns.url, ns.call, ns.arguments))
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # includes the ExceptionGroup raised by the anyio TaskGroup
        message = describe_failure(e)
        if message is None:
            raise
        print(message, file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
