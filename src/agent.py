"""Minimal LangGraph agent: GreenNode AIP LLM + tools from the MCP Gateway.

    python src/agent.py "What is the VNM stock price today?"

Env: MCP_GATEWAY_URLS (multiple connector URLs, comma-separated), GATEWAY_AUTH..., LLM_API_KEY,
LLM_MODEL, LLM_BASE_URL. See .env.example.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gateway_auth import auth_headers  # noqa: E402

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent
except ImportError as e:  # pragma: no cover
    sys.exit(f"Missing dependency ({e}). Run: pip install -r requirements.txt")

DEFAULT_LLM_BASE_URL = "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"
SYSTEM_PROMPT = "You are a helpful assistant. Use tools when you need real data. Answer in the user's language."


def gateway_urls() -> list[str]:
    raw = os.environ.get("MCP_GATEWAY_URLS") or os.environ.get("MCP_GATEWAY_URL", "")
    return [u.strip() for u in raw.split(",") if u.strip()]


def build_servers(urls: list[str], headers: dict[str, str]) -> dict:
    """Each connector URL = one MCP 'server'; tool names come from the gateway."""
    return {
        f"gw{i}": {"transport": "streamable_http", "url": url, "headers": headers}
        for i, url in enumerate(urls)
    }


async def main(question: str) -> None:
    urls = gateway_urls()
    if not urls:
        sys.exit("Missing MCP_GATEWAY_URLS (connector URLs on the gateway, comma-separated).")

    client = MultiServerMCPClient(build_servers(urls, auth_headers()))
    tools = await client.get_tools()  # tools/list qua gateway
    print(f"Loaded {len(tools)} tool(s): {', '.join(t.name for t in tools)}\n")

    llm = ChatOpenAI(
        model=os.environ.get("LLM_MODEL", "z-ai/glm-5.3-flash"),
        base_url=os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
        api_key=os.environ["LLM_API_KEY"],
    )
    agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
    result = await agent.ainvoke({"messages": [("user", question)]})  # tools/call qua gateway
    print(result["messages"][-1].content)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Usage: python src/agent.py "your question"')
    asyncio.run(main(" ".join(sys.argv[1:])))
