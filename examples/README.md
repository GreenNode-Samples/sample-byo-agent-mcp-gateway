# MCP desktop client configuration

- `claude_desktop_config.json` -> merge into `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS).
- `cursor_mcp.json` -> `~/.cursor/mcp.json` (or `.cursor/mcp.json` in your project).

Both use [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) (pinned to `0.14.3`) as a stdio -> streamable HTTP bridge.
They send the header `Authorization: Bearer <token>`, with the whole value (`Bearer <token>`) kept in the `AUTH_HEADER`
variable of the `env` block. `mcp-remote` expands `${AUTH_HEADER}` itself, and the `Authorization:${AUTH_HEADER}` form
(no space after the colon) avoids spaces inside `args`, which Cursor and Claude Desktop on Windows mangle when they start `npx`.
Spaces are fine inside environment variables.

> **IAM tokens expire after ~30 minutes.** For a quick trial: run `python scripts/print_token.py`, paste the output after
> `Bearer ` in `env.AUTH_HEADER`, and restart the client. For long-term use, choose one of the following:
> 1. Configure the gateway with **Inbound Auth = JWT** and use a JWT from your IdP (long-lived token / with refresh);
> 2. Keep the token in a file that a scheduled job refreshes, and pass `--header-file` instead of `--header`
>    (see below). The token does not appear in the process list.
>
> Do not commit files that contain a filled-in token. The `--header`, `--header-file` and `--transport http-only` flags are
> documented in the `mcp-remote` 0.14.3 README.

## Refreshing the token with `--header-file`

`python scripts/print_token.py --header-file PATH` writes `Authorization: Bearer <token>` to `PATH` atomically with mode 600
(the format `mcp-remote --header-file` expects: one `Name: value` per line). Replace the last two `args` entries of the example with:

```json
"--header-file",
"/Users/you/.config/agentbase/headers.txt"
```

and drop the `env` block. Run the script every 20 minutes. `mcp-remote` may read the file only when it starts: if the client
still sends an expired token, restart the client.

cron (macOS / Linux), after `crontab -e`:

```cron
*/20 * * * * cd /path/to/sample-byo-agent-mcp-gateway && set -a && . ./.env && set +a && .venv/bin/python scripts/print_token.py --header-file "$HOME/.config/agentbase/headers.txt"
```

launchd (macOS), `~/Library/LaunchAgents/vn.greennode.agentbase-token.plist`, then `launchctl load` it:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>vn.greennode.agentbase-token</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>cd /path/to/sample-byo-agent-mcp-gateway &amp;&amp; set -a &amp;&amp; . ./.env &amp;&amp; set +a &amp;&amp; .venv/bin/python scripts/print_token.py --header-file "$HOME/.config/agentbase/headers.txt"</string>
  </array>
  <key>StartInterval</key><integer>1200</integer>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
```
