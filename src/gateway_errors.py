"""Error helpers shared by the CLI (`list_tools.py`) and the agent (`agent.py`).

- `ConfigError`: a problem with the local configuration (env vars, URLs, credentials).
- Mapping of gateway failures (HTTP status, network errors) to an actionable hint, including when the
  `mcp` SDK wraps them in an `ExceptionGroup` (anyio TaskGroup) or reports them as JSON-RPC errors.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlparse

import httpx
from mcp.shared.exceptions import McpError

# Keys of `McpError.error.data` set by `gateway_client.GatewayHttpClient` when it turns a failed HTTP
# request into a JSON-RPC error (so one failed call does not tear down the shared MCP session).
HTTP_STATUS_KEY = "http_status"
NETWORK_ERROR_KEY = "network_error"  # "timeout" or "network"

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
    404: (
        "404 Not Found — wrong connector path in the gateway URL (MCP_GATEWAY_URL / MCP_GATEWAY_URLS: "
        "…/<connector>). Re-copy it from the gateway detail page."
    ),
}


class ConfigError(RuntimeError):
    """Local configuration is missing or invalid (reported without a traceback)."""


def check_http_url(name: str, value: str) -> str:
    """Return `value` if it is a usable http(s) URL, else raise `ConfigError` (no network call)."""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or "<" in value or ">" in value:
        raise ConfigError(
            f"{name}='{value}' is not a valid http(s) URL (placeholders such as <gateway> must be replaced). "
            "Gateway connector URLs look like https://gw-<gateway>-<id>.agentbase-gateway.aiplatform.vngcloud.vn/<connector>."
        )
    return value


def describe_http_error(status: int) -> str:
    if status in HTTP_HINTS:
        return HTTP_HINTS[status]
    if status >= 500:
        return f"{status} — gateway/MCP server error. Retry later; if it persists, check the connector/runtime logs."
    return f"HTTP {status} — unexpected response from the gateway."


def _walk(exc: BaseException) -> Iterator[BaseException]:
    """Walk an exception, including ExceptionGroup members and __cause__ chains."""
    seen: set[int] = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        stack.extend(getattr(e, "exceptions", ()))
        if e.__cause__ is not None:
            stack.append(e.__cause__)


def leaf_exception(exc: BaseException) -> BaseException:
    """The first innermost exception of (nested) ExceptionGroups: what actually went wrong."""
    while sub := getattr(exc, "exceptions", None):
        exc = sub[0]
    return exc


def _rpc_data(exc: BaseException) -> dict:
    """`error.data` of an McpError (a dict, possibly empty)."""
    data = exc.error.data if isinstance(exc, McpError) else None
    return data if isinstance(data, dict) else {}


def find_config_error(exc: BaseException) -> ConfigError | None:
    return next((e for e in _walk(exc) if isinstance(e, ConfigError)), None)


def find_http_status(exc: BaseException) -> int | None:
    """HTTP status that caused the error. The `mcp` SDK reports a 404 as McpError 'Session terminated'."""
    for e in _walk(exc):
        if isinstance(e, httpx.HTTPStatusError):
            return e.response.status_code
        if isinstance(status := _rpc_data(e).get(HTTP_STATUS_KEY), int):
            return status
        if isinstance(e, McpError) and "session terminated" in str(e).lower():
            return 404
    return None


def find_network_error(exc: BaseException) -> BaseException | None:
    return next(
        (
            e
            for e in _walk(exc)
            if isinstance(e, httpx.ConnectError | httpx.TimeoutException) or NETWORK_ERROR_KEY in _rpc_data(e)
        ),
        None,
    )


def is_timeout(exc: BaseException) -> bool:
    return any(
        isinstance(e, httpx.TimeoutException) or _rpc_data(e).get(NETWORK_ERROR_KEY) == "timeout"
        for e in _walk(exc)
    )


def describe_failure(exc: BaseException) -> str | None:
    """One-line, actionable message for a known failure (configuration, HTTP status, network); None otherwise."""
    # Order matters: an IAM failure keeps the HTTP error as __cause__, but it is not a gateway Inbound Auth problem.
    if (config_error := find_config_error(exc)) is not None:
        return f"Configuration error: {config_error}"
    if (status := find_http_status(exc)) is not None:
        return f"Error: {describe_http_error(status)}"
    if (net := find_network_error(exc)) is not None:
        return f"Network error: {net}. Check the URL; a Private gateway is only reachable from your private network."
    return None
