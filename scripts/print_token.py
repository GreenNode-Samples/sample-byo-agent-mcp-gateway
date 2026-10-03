"""Print a fresh IAM token (stdout) for use with Claude Desktop / Cursor / curl.

    set -a; source .env; set +a
    export GATEWAY_TOKEN=$(python scripts/print_token.py)

IAM tokens expire after ~30 minutes — rerun when needed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from gateway_auth import AuthConfigError, get_iam_token  # noqa: E402

if __name__ == "__main__":
    try:
        print(get_iam_token(force=True))
    except AuthConfigError as e:
        sys.exit(f"Error: {e}")
