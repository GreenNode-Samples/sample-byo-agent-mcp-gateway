import pytest

pytest.importorskip("langchain_mcp_adapters")
pytest.importorskip("langgraph")

import agent  # noqa: E402


def test_gateway_urls_split(monkeypatch):
    monkeypatch.setenv("MCP_GATEWAY_URLS", "https://a/x, https://b/y ,")
    assert agent.gateway_urls() == ["https://a/x", "https://b/y"]


def test_build_servers():
    s = agent.build_servers(["https://a/x", "https://b/y"], {"Authorization": "Bearer t"})
    assert s["gw1"] == {"transport": "streamable_http", "url": "https://b/y",
                        "headers": {"Authorization": "Bearer t"}}
