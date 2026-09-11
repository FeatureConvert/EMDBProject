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
        payload = {
            "authenticated": False,
            "reason": "No refresh token stored. Run login (see references/setup_checklist.md).",
            "config_path": str(config.config_path),
        }
    else:
        payload = {
            "authenticated": dsp.check_auth_key(config),
            "hostname": config.hostname,
            "config_path": str(config.config_path),
        }

    print_json(payload)
    # One exit-code decision, derived from the same value the JSON reports -
    # body and exit code can't disagree, and a future not-authenticated
    # branch can't forget its own sys.exit(1) (the per-branch inconsistency
    # commit 791aef3 had to fix by hand).
    if not payload["authenticated"]:
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
    # Handlers wired via set_defaults(func=...) - see em_deposit.main() for
    # why this beats a hand-mirrored if/elif dispatch chain.
    sub.add_parser("check").set_defaults(func=lambda args: cmd_check())
    sub.add_parser("login").set_defaults(func=lambda args: cmd_login())
    args = parser.parse_args()

    run_cli(lambda: args.func(args))


if __name__ == "__main__":
    main()
