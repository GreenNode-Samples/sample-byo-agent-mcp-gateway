# Cấu hình client MCP desktop

- `claude_desktop_config.json` → gộp vào `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS).
- `cursor_mcp.json` → `~/.cursor/mcp.json` (hoặc `.cursor/mcp.json` trong project).

Cả hai dùng [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) làm cầu nối stdio → streamable HTTP, gắn header
`Authorization: Bearer ${GATEWAY_TOKEN}` (biến `GATEWAY_TOKEN` khai báo trong khối `env`; cú pháp `--header "Name: value"`
theo README của mcp-remote).

> **Token IAM hết hạn sau ~30 phút.** Với mục đích thử nhanh: chạy `python scripts/print_token.py`, dán vào `env.GATEWAY_TOKEN`,
> restart client. Dùng lâu dài nên chọn một trong hai:
> 1. Cấu hình gateway **Inbound Auth = JWT** và dùng JWT từ IdP của bạn (token dài hạn / có refresh);
> 2. Chạy script refresh (cron/launchd) ghi token ra file, rồi dùng `--header-file /path/headers.txt`
>    của mcp-remote (mỗi dòng `Authorization: Bearer <token>`) — token không lộ trong process list.
>
> Không commit file đã điền token. Flag `--header` / `--header-file` / `--transport http-only` đã đối chiếu với README mcp-remote 0.14.3;
> nếu client của bạn không nội suy `${...}` trong args, đặt cả giá trị vào một biến env và dùng `Authorization:${AUTH_HEADER}` (không có khoảng trắng sau dấu `:`).
