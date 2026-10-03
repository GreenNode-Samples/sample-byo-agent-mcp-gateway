# MCP desktop client configuration

- `claude_desktop_config.json` → merge into `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS).
- `cursor_mcp.json` → `~/.cursor/mcp.json` (or `.cursor/mcp.json` in your project).

Both use [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) as a stdio → streamable HTTP bridge, attaching the header
`Authorization: Bearer ${GATEWAY_TOKEN}` (the `GATEWAY_TOKEN` variable is declared in the `env` block; the `--header "Name: value"` syntax
follows the mcp-remote README).

> **IAM tokens expire after ~30 minutes.** For a quick trial: run `python scripts/print_token.py`, paste the output into `env.GATEWAY_TOKEN`,
> and restart the client. For long-term use, choose one of the following:
> 1. Configure the gateway with **Inbound Auth = JWT** and use a JWT from your IdP (long-lived token / with refresh);
> 2. Run a refresh script (cron/launchd) that writes the token to a file, then use mcp-remote's `--header-file /path/headers.txt`
>    (each line `Authorization: Bearer <token>`) — the token does not appear in the process list.
>
> Do not commit files that contain a filled-in token. The `--header` / `--header-file` / `--transport http-only` flags were checked against the mcp-remote 0.14.3 README;
> if your client does not interpolate `${...}` in args, put the whole value in an env variable and use `Authorization:${AUTH_HEADER}` (no space after the `:`).
