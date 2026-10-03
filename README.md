# 🔌 MCP Gateway Client — gọi tool AgentBase từ agent chạy BÊN NGOÀI

> Sample client cho thấy **agent/app của chính bạn** (laptop, server riêng, cloud khác — *không* chạy trên AgentBase Runtime)
> gọi tool qua **GreenNode MCP Gateway**: mọi lời gọi vẫn đi qua **xác thực → Policy Group → Outbound Auth → MCP server**,
> nên bạn có governance (auth, policy, audit) của AgentBase cho tool dù agent nằm ở đâu.

[![CI](https://github.com/GreenNode-Sample-AgentBase/greennode-agentbase-mcp-gateway-client/actions/workflows/ci.yml/badge.svg)](https://github.com/GreenNode-Sample-AgentBase/greennode-agentbase-mcp-gateway-client/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Vì sao repo này tồn tại?

Các sample khác ([`travel-buddy`](../greennode-agentbase-sample-travel-buddy), [`mcp-stock-server`](../greennode-agentbase-sample-mcp-stock-server))
chạy *trên* AgentBase, nơi runtime được inject sẵn service account. Nhưng nhiều khách hàng đã có agent riêng
(LangGraph, Claude Desktop, Cursor, ứng dụng nội bộ…) và chỉ muốn **dùng gateway làm cổng tool có kiểm soát**:

- **Một cổng duy nhất** tới nhiều MCP server — agent không cầm API key của từng dịch vụ (connector lo Outbound Auth).
- **Policy Group**: quyết định ai được gọi `<connector>__<tool>` nào — bất kể agent chạy ở đâu.
- **Audit tập trung** tại gateway.

## 🏗 Kiến trúc

```
 Agent của bạn (laptop / server / cloud khác)
   ├─ LangGraph agent (src/agent.py)  ├─ CLI (src/list_tools.py)  └─ Claude Desktop / Cursor (mcp-remote)
        │  MCP streamable HTTP (JSON-RPC: tools/list, tools/call)
        │  Authorization: Bearer <IAM token | JWT từ IdP của bạn>
        ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ MCP Gateway  https://gw-<gateway>-<id>.agentbase-gateway…/<connector>
 │  ① Inbound Auth  — IAM Permissions | JWT | (No authorization: chỉ dev)
 │  ② Policy Group  — first match wins; không rule khớp → 403
 │                    (tools/list bỏ qua policy, tools/call được kiểm tra)
 │  ③ Connector     — Outbound Auth (API Key / OAuth / none) do gateway giữ
 └──────────────────────────────┬───────────────────────────────┘
                                ▼
                           MCP server (vd: connector `stock` của repo mcp-stock-server)
```

Lời gọi **LLM là đường riêng**: agent của bạn gọi LLM (GreenNode AI Platform hoặc bất kỳ LLM OpenAI-compatible nào) trực tiếp, không qua gateway.

## Cấu trúc

| File | Việc |
|---|---|
| `src/gateway_auth.py` | `get_iam_token()` (cache, thread-safe, margin 60s) · `auth_headers()` cho `GATEWAY_AUTH=iam\|jwt\|none` |
| `src/list_tools.py` | CLI dùng SDK `mcp`: liệt kê tool, `--call` gọi 1 tool, giải thích lỗi 401/403/404 |
| `src/agent.py` | Agent LangGraph (`create_react_agent`) nạp tool từ 1+ connector qua `langchain-mcp-adapters` |
| `scripts/print_token.py` | In IAM token mới cho Claude Desktop / Cursor / curl |
| `examples/` | Config `mcp-remote` cho Claude Desktop & Cursor |
| `tests/` | pytest hermetic (không network) |

## Cài đặt từng bước

### 1. Cấu hình Inbound Auth trên gateway

Console → AgentBase → **MCP Gateway** → gateway của bạn → *Inbound Auth* (skill `agentbase-gateway` làm được qua CLI). Chọn **một**:

| Chế độ trên gateway | `GATEWAY_AUTH` | Bạn cần |
|---|---|---|
| **IAM Permissions** | `iam` (mặc định) | Tạo **IAM service account** (Console → IAM → Service accounts) → lấy `client_id` / `client_secret`. Code đổi sang Bearer token bằng client-credentials. |
| **JWT** (mặc định của gateway) | `jwt` | Khai báo IdP của bạn (Okta, Auth0, Keycloak, Entra ID…) qua **Discovery URL** hoặc **JWKS**; chọn claim làm principal (mặc định `sub`). Code chỉ gửi JWT bạn đưa vào `GATEWAY_JWT` / `GATEWAY_JWT_FILE`. |
| **No authorization** | `none` | Chỉ dùng dev — ai biết URL cũng gọi được. |

> Ngoài AgentBase, `GREENNODE_CLIENT_ID` / `GREENNODE_CLIENT_SECRET` **không** được tự inject như trên Runtime — bạn tự tạo và quản lý.

### 2. Lấy URL connector

Trang chi tiết gateway → connector → copy endpoint dạng
`https://gw-<gateway>-<id>.agentbase-gateway.aiplatform.vngcloud.vn/<connector>` (vd `/stock`).

### 3. Cho phép principal của bạn trong Policy Group

Gateway **từ chối mặc định**: chưa gắn Policy Group hoặc không rule nào khớp ⇒ `tools/call` trả `403` (`tools/list` thì luôn được phép).
Principal: `iam:<định danh service account>` với IAM, hoặc giá trị claim đã cấu hình (vd `sub`) với JWT. Action có dạng `<connector>__<tool>`:

```json
{
  "effect": "allow",
  "principal": "iam:<service-account-của-bạn>",
  "actions": ["stock__stock_quote", "stock__top_gainers", "stock__valuation"],
  "resources": ["gateway:<tên-gateway>"]
}
```

Xem skill `agentbase-policy`. Nếu chưa biết principal chính xác, gọi thử một tool — gateway sẽ từ chối (403) và audit ghi lại principal để bạn đối chiếu. *Verify cú pháp principal với phiên bản gateway của bạn.*

### 4. Cài và cấu hình môi trường

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # điền MCP_GATEWAY_URL(S), GREENNODE_CLIENT_ID/SECRET (hoặc GATEWAY_JWT), LLM_API_KEY
set -a; source .env; set +a
```

## Chạy

### A. Liệt kê / gọi tool (không cần LLM)

```bash
python src/list_tools.py
python src/list_tools.py --call stock__stock_quote --args '{"symbol":"VNM"}'
```

Dùng đúng tên tool mà `tools/list` in ra (tiền tố connector tuỳ phiên bản gateway; *policy action luôn là `<connector>__<tool>`*).

### B. Agent LangGraph

```bash
python src/agent.py "Cổ phiếu nào tăng mạnh nhất hôm nay và định giá của nó ra sao?"
```

`MCP_GATEWAY_URLS` nhận nhiều connector (cách nhau dấu phẩy) → agent thấy tool của tất cả. LLM mặc định: GreenNode AI Platform
(`https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1`, model `z-ai/glm-5.3-flash`); đổi `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` để dùng LLM OpenAI-compatible khác.

### C. Claude Desktop / Cursor

```bash
set -a; source .env; set +a
python scripts/print_token.py     # dán vào env.GATEWAY_TOKEN trong examples/*.json
```

Copy `examples/claude_desktop_config.json` hoặc `examples/cursor_mcp.json` vào config của client (chi tiết trong [examples/README.md](examples/README.md)).
Token IAM hết hạn ~30 phút; dùng lâu dài nên chọn JWT từ IdP của bạn hoặc script refresh + `--header-file`.

## Troubleshooting

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `401 Unauthorized` | Inbound Auth từ chối: sai `GATEWAY_AUTH` so với gateway, token hết hạn, JWT sai issuer/audience/JWKS, sai client id/secret | Khớp chế độ; chạy lại để lấy token mới; kiểm tra cấu hình Discovery URL/JWKS |
| `403 Forbidden` | Xác thực OK nhưng **Policy Group** không cho principal này gọi `<connector>__<tool>` (hoặc chưa gắn Policy Group) | Thêm rule allow đúng principal + action; nhớ first match wins |
| `tools/list` được nhưng `tools/call` 403 | Đúng thiết kế: `tools/list` bỏ qua policy | Như dòng trên |
| `404` / `Session terminated` | Sai đường dẫn connector trong URL | Copy lại endpoint từ trang chi tiết gateway (`…/<connector>`) |
| Timeout / `All connection attempts failed` | Gateway **Private** chỉ truy cập được từ mạng riêng của bạn (VPN/VPC); hoặc chặn egress | Chạy client trong mạng được nối tới gateway, hoặc dùng gateway Public + Inbound Auth + IP allowlist |
| `Thiếu GREENNODE_CLIENT_ID…` | Chưa đặt credential (không tự inject ngoài AgentBase) | Điền `.env`, `source` lại |
| `5xx` | Lỗi connector / MCP server phía sau | Xem log runtime của MCP server |

## Bảo mật

- **Không commit secret**: `.env` đã nằm trong `.gitignore`; `client_secret`, JWT, `GATEWAY_TOKEN` không đưa vào config đẩy lên git.
- **Xoay (rotate)** client secret định kỳ; thu hồi service account khi không dùng.
- **Least-privilege**: mỗi agent/service account một principal riêng, policy chỉ liệt kê đúng các action cần thiết — tránh `"*"`.
- Không dùng *No authorization* ngoài môi trường dev; với gateway Public nên thêm giới hạn nguồn truy cập.
- Cấu hình client desktop: ưu tiên `--header-file` để token không hiện trong danh sách tiến trình.

## Test

```bash
pip install -r requirements.txt pytest
python -m pytest tests/ -q   # hermetic — không gọi network thật
```

Bao gồm: cache & margin 60s của IAM token, `auth_headers` theo từng chế độ, JWT từ file, thiếu credential, parse tham số CLI,
và ánh xạ lỗi 401/403/404/mạng (kể cả khi SDK bọc trong `ExceptionGroup`).

## Kết hợp với sample khác

Dùng làm đích gọi: [`greennode-agentbase-sample-mcp-stock-server`](../greennode-agentbase-sample-mcp-stock-server) — đăng ký connector `stock`
vào gateway, cho phép các action `stock__<tool>` ở Policy Group rồi trỏ `MCP_GATEWAY_URL` vào `…/stock`.

## Tài nguyên liên quan

- Skill `agentbase-gateway` — gateway, connector, Inbound/Outbound Auth
- Skill `agentbase-policy` — viết policy `<connector>__<tool>`
- Skill `agentbase-llm` — API key LLM AI Platform

## License

[MIT](LICENSE)
