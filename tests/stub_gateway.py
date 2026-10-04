"""In-process stand-in for an MCP Gateway connector (localhost only, no real network).

A FastMCP server sits behind a gate that emulates the gateway: Inbound Auth (401 for an unknown token)
and Policy (403 for denied tools on `tools/call`). Every request is recorded in `StubGateway.requests`.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount


@dataclass
class Seen:
    rpc_method: str | None
    tool: str | None
    authorization: str


class StubGateway:
    DENIED_TOOLS = {"denied_tool"}

    def __init__(self) -> None:
        self.requests: list[Seen] = []
        self.valid_tokens: set[str] | None = None  # None = no Inbound Auth
        self.fail_next: list[int] = []  # HTTP statuses returned for the next `tools/call` requests
        self.port = 0
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    # -- tools served behind the gate ---------------------------------------------------------
    def _build_mcp(self) -> FastMCP:
        mcp = FastMCP("stock", streamable_http_path="/")

        @mcp.tool()
        def stock_quote(symbol: str) -> str:
            """Latest price of a stock."""
            return json.dumps({"symbol": symbol, "price": 61000})

        @mcp.tool()
        def fail_tool(symbol: str) -> str:
            """Always fails (isError=true)."""
            raise ValueError(f"upstream broke for {symbol}")

        @mcp.tool()
        def big_tool(symbol: str) -> str:
            """Returns an oversized result."""
            return "X" * 100_000

        @mcp.tool()
        def denied_tool(symbol: str) -> str:
            """Denied by the gateway policy."""
            return "unreachable"

        return mcp

    # -- the gate -------------------------------------------------------------------------------
    def _gate(self, app):
        async def gate(scope, receive, send):
            if scope["type"] != "http":
                return await app(scope, receive, send)
            messages, body, more = [], b"", True
            while more:
                message = await receive()
                messages.append(message)
                body += message.get("body", b"")
                more = message.get("more_body", False)
            rpc_method = tool = None
            with contextlib.suppress(ValueError):
                rpc = json.loads(body)
                rpc_method, tool = rpc.get("method"), (rpc.get("params") or {}).get("name")
            authorization = dict(scope["headers"]).get(b"authorization", b"").decode()
            self.requests.append(Seen(rpc_method, tool, authorization))

            if self.valid_tokens is not None and authorization.removeprefix("Bearer ") not in self.valid_tokens:
                return await Response("unauthorized", status_code=401)(scope, receive, send)
            if rpc_method == "tools/call" and self.fail_next:
                return await Response("upstream error", status_code=self.fail_next.pop(0))(scope, receive, send)
            if rpc_method == "tools/call" and tool in self.DENIED_TOOLS:
                return await Response("forbidden by policy", status_code=403)(scope, receive, send)

            replay = iter(messages)

            async def replay_receive():
                return next(replay, None) or await receive()

            return await app(scope, replay_receive, send)

        return gate

    # -- lifecycle ------------------------------------------------------------------------------
    def start(self) -> None:
        mcp = self._build_mcp()
        gate = self._gate(mcp.streamable_http_app())

        @contextlib.asynccontextmanager
        async def lifespan(_app):
            async with mcp.session_manager.run():
                yield

        # The same connector is also mounted as `stock-copy` to test duplicate tool names.
        app = Starlette(routes=[Mount("/stock", app=gate), Mount("/stock-copy", app=gate)], lifespan=lifespan)
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("stub gateway did not start")
            time.sleep(0.02)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    def reset(self) -> None:
        self.requests.clear()
        self.valid_tokens = None
        self.fail_next.clear()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/stock"

    @property
    def copy_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/stock-copy"

    def tool_calls(self) -> list[Seen]:
        return [r for r in self.requests if r.rpc_method == "tools/call"]
