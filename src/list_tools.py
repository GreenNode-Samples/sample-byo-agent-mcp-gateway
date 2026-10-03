"""CLI: liệt kê / gọi tool qua MCP Gateway của GreenNode AgentBase.

    python src/list_tools.py                              # tools/list
    python src/list_tools.py --call stock_quote --args '{"symbol":"VNM"}'

Tên tool là tên ĐẦY ĐỦ như gateway trả về trong tools/list (xem README về tiền tố connector).
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
        "401 Unauthorized — Inbound Auth của gateway từ chối credential. Kiểm tra: GATEWAY_AUTH khớp "
        "chế độ trên gateway (IAM Permissions hay JWT); token chưa hết hạn; với JWT: issuer/audience/"
        "JWKS khớp cấu hình gateway."
    ),
    403: (
        "403 Forbidden — xác thực OK nhưng Policy Group từ chối principal này gọi tool này "
        "(first match wins, không rule nào khớp => 403). Thêm rule allow cho principal của bạn "
        "(iam:<service-account> hoặc claim JWT đã cấu hình) với action '<connector>__<tool>'. "
        "Lưu ý: tools/list không bị policy chặn, chỉ tools/call."
    ),
    404: "404 Not Found — sai đường dẫn connector trong MCP_GATEWAY_URL (…/<connector>). Copy lại từ trang chi tiết gateway.",
}


def describe_http_error(status: int) -> str:
    if status in HTTP_HINTS:
        return HTTP_HINTS[status]
    if status >= 500:
        return f"{status} — lỗi phía gateway/MCP server. Thử lại sau; nếu kéo dài, xem log của connector/runtime."
    return f"HTTP {status} — phản hồi không mong đợi từ gateway."


def _walk(exc: BaseException):
    """Duyệt exception, kể cả ExceptionGroup (anyio TaskGroup) và __cause__."""
    yield exc
    for sub in getattr(exc, "exceptions", ()):
        yield from _walk(sub)
    if exc.__cause__ is not None:
        yield from _walk(exc.__cause__)


def find_http_status(exc: BaseException) -> int | None:
    """HTTP status gây lỗi. SDK `mcp` báo 404 thành McpError 'Session terminated'."""
    for e in _walk(exc):
        if isinstance(e, httpx.HTTPStatusError):
            return e.response.status_code
        if isinstance(e, McpError) and "session terminated" in str(e).lower():
            return 404
    return None


def find_network_error(exc: BaseException) -> BaseException | None:
    return next((e for e in _walk(exc) if isinstance(e, (httpx.ConnectError, httpx.TimeoutException))), None)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Liệt kê / gọi tool qua GreenNode MCP Gateway")
    p.add_argument("--url", default=os.environ.get("MCP_GATEWAY_URL"),
                   help="URL connector trên gateway (mặc định: env MCP_GATEWAY_URL)")
    p.add_argument("--call", metavar="TOOL", help="Gọi 1 tool thay vì chỉ liệt kê")
    p.add_argument("--args", default="{}", help="Arguments JSON cho --call, vd '{\"symbol\":\"VNM\"}'")
    ns = p.parse_args(argv)
    if not ns.url:
        p.error("thiếu URL: đặt MCP_GATEWAY_URL hoặc truyền --url")
    try:
        ns.arguments = json.loads(ns.args)
    except json.JSONDecodeError as e:
        p.error(f"--args không phải JSON hợp lệ: {e}")
    if not isinstance(ns.arguments, dict):
        p.error("--args phải là JSON object")
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
                    print("\n[tool trả isError=true]", file=sys.stderr)
                    sys.exit(2)
                return
            tools = (await session.list_tools()).tools
            print(f"{len(tools)} tool(s) tại {url}\n")
            for t in tools:
                desc = (t.description or "").strip().splitlines()
                print(f"- {t.name}: {desc[0] if desc else ''}")


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    try:
        asyncio.run(run(ns.url, ns.call, ns.arguments))
    except AuthConfigError as e:
        print(f"Lỗi cấu hình: {e}", file=sys.stderr)
        return 1
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        status = find_http_status(e)
        if status is not None:
            print(f"Lỗi: {describe_http_error(status)}", file=sys.stderr)
        elif (net := find_network_error(e)) is not None:
            print(f"Lỗi mạng: {net}. Kiểm tra URL; gateway Private chỉ truy cập được từ mạng riêng của bạn.",
                  file=sys.stderr)
        else:
            raise
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
