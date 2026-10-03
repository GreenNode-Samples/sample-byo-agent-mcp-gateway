"""Agent LangGraph tối giản: LLM GreenNode AIP + tools từ MCP Gateway.

    python src/agent.py "Giá cổ phiếu VNM hôm nay?"

Env: MCP_GATEWAY_URLS (nhiều URL connector, cách nhau dấu phẩy), GATEWAY_AUTH..., LLM_API_KEY,
LLM_MODEL, LLM_BASE_URL. Xem .env.example.
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
    sys.exit(f"Thiếu dependency ({e}). Chạy: pip install -r requirements.txt")

DEFAULT_LLM_BASE_URL = "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"
SYSTEM_PROMPT = "Bạn là trợ lý hữu ích. Dùng tools khi cần dữ liệu thật và trả lời bằng tiếng Việt."


def gateway_urls() -> list[str]:
    raw = os.environ.get("MCP_GATEWAY_URLS") or os.environ.get("MCP_GATEWAY_URL", "")
    return [u.strip() for u in raw.split(",") if u.strip()]


def build_servers(urls: list[str], headers: dict[str, str]) -> dict:
    """Mỗi URL connector = một 'server' MCP; tên tool lấy từ gateway."""
    return {
        f"gw{i}": {"transport": "streamable_http", "url": url, "headers": headers}
        for i, url in enumerate(urls)
    }


async def main(question: str) -> None:
    urls = gateway_urls()
    if not urls:
        sys.exit("Thiếu MCP_GATEWAY_URLS (URL connector trên gateway, cách nhau dấu phẩy).")

    client = MultiServerMCPClient(build_servers(urls, auth_headers()))
    tools = await client.get_tools()  # tools/list qua gateway
    print(f"Đã nạp {len(tools)} tool: {', '.join(t.name for t in tools)}\n")

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
        sys.exit('Cách dùng: python src/agent.py "câu hỏi của bạn"')
    asyncio.run(main(" ".join(sys.argv[1:])))
