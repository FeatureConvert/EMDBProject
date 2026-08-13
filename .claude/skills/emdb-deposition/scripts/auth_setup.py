"""Check or bootstrap wwPDB OneDep authentication for onedep_lib.

onedep_lib does NOT implement the ORCID OAuth browser flow itself - it only
stores and refreshes tokens. The initial refresh token must come from the
wwPDB OneDep web portal:

  1. Go to https://deposit-pdbe.wwpdb.org/deposition/ and "Sign in with ORCID".
  2. Scroll to the "Deposition API" section and generate an API refresh
     token. It is a 30-day token shown ONCE - copy it immediately.

Usage (run from the project root, using the project's venv):
  .venv/bin/python3 .claude/skills/emdb-deposition/scripts/auth_setup.py check
  ONEDEP_REFRESH_TOKEN=<pasted-token> .venv/bin/python3 \
      .claude/skills/emdb-deposition/scripts/auth_setup.py login

`login` reads the token from the ONEDEP_REFRESH_TOKEN environment variable
only - never as a CLI argument (shell history / process list exposure) and
never typed into chat for Claude to store. After a successful login the
token is persisted to ~/.config/onedep/config.toml and rotates automatically
on every future use, so the env var must be unset afterward or it will keep
overriding the rotated on-disk token with the stale original.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import JsonArgumentParser, fail, print_json, run_cli  # noqa: E402


def cmd_check() -> None:
    import onedep_lib as dsp
    from onedep_lib.config import DepositConfig

    config = DepositConfig.load()
    if config.refresh_token is None:
        print_json(
            {
                "authenticated": False,
                "reason": "No refresh token stored. Run login (see references/setup_checklist.md).",
                "config_path": str(config.config_path),
            }
        )
        # Exit nonzero here too, matching the "stored token is invalid" branch
        # below - both report authenticated: false, so a caller checking the
        # exit code (not just parsing the JSON body) shouldn't see success
        # for one cause of "not authenticated" but failure for the other.
        sys.exit(1)

    ok = dsp.check_auth_key(config)
    print_json(
        {
            "authenticated": ok,
            "hostname": config.hostname,
            "config_path": str(config.config_path),
        }
    )
    if not ok:
        sys.exit(1)


def cmd_login() -> None:
    from onedep_lib.auths.token import TokenStore
    from onedep_lib.config import DepositConfig
    from onedep_lib.exceptions import OneDepError

    token = os.environ.get("ONEDEP_REFRESH_TOKEN")
    if not token:
        fail(
            "ONEDEP_REFRESH_TOKEN is not set. Generate a token from the "
            "'Deposition API' section at https://deposit-pdbe.wwpdb.org/deposition/ "
            "(after signing in with ORCID), then re-run as: "
            "ONEDEP_REFRESH_TOKEN=<token> .venv/bin/python3 "
            ".claude/skills/emdb-deposition/scripts/auth_setup.py login"
        )

    config = DepositConfig.load()  # picks up ONEDEP_REFRESH_TOKEN automatically
    store = TokenStore(config)
    try:
        store.refresh()
    except OneDepError as exc:
        fail(f"Login failed: {exc}")
        return

    print_json(
        {
            "success": True,
            "hostname": config.hostname,
            "config_path": str(config.config_path),
            "next_step": (
                "Run `unset ONEDEP_REFRESH_TOKEN` in your shell now. The token "
                "rotates on every use and is persisted to config_path above - "
                "leaving the env var set will override the rotated token with "
                "the stale original and break future logins."
            ),
        }
    )


def main() -> None:
    parser = JsonArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("login")
    args = parser.parse_args()

    def dispatch() -> None:
        if args.command == "check":
            cmd_check()
        elif args.command == "login":
            cmd_login()

    run_cli(dispatch)


if __name__ == "__main__":
    main()
