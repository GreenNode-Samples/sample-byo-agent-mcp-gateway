"""Get a fresh IAM token for Claude Desktop / Cursor / curl.

    set -a; source .env; set +a
    export AUTH_HEADER="Bearer $(python scripts/print_token.py)"                  # bare token on stdout
    python scripts/print_token.py --header-file ~/.config/agentbase/headers.txt   # "Authorization: Bearer <token>"

`--header-file` writes the file atomically with mode 600, for `mcp-remote --header-file` (see examples/README.md).
IAM tokens expire after ~30 minutes, so run the script on a schedule (cron / launchd) to keep the file fresh.
"""

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from gateway_auth import AuthConfigError, get_iam_token  # noqa: E402


def write_header_file(path: str, token: str) -> None:
    """Replace `path` with `Authorization: Bearer <token>` (temp file + rename, so readers never see a partial file)."""
    path = os.path.abspath(os.path.expanduser(path))
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".headers-", suffix=".tmp")
    try:
        os.chmod(tmp_path, 0o600)  # mkstemp already creates the file private; make the intent explicit
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"Authorization: Bearer {token}\n")
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print a fresh IAM token, or write it to a header file")
    parser.add_argument("--header-file", metavar="PATH",
                        help="write 'Authorization: Bearer <token>' to PATH (atomic, mode 600) instead of printing the token")
    ns = parser.parse_args(argv)
    try:
        token = get_iam_token(force=True)
        if ns.header_file:
            write_header_file(ns.header_file, token)
        else:
            print(token)
    except AuthConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Error: could not write {ns.header_file}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
