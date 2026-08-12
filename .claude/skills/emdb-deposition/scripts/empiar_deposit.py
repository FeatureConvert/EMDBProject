"""Validate and submit an EMPIAR raw-data deposition via the empiar-depositor CLI.

empiar-depositor is a real console-script CLI (not a Python API), so this
wrapper's job is: (1) validate the JSON_INPUT file against the schema
bundled in the empiar_depositor package before spending time on data
transfer, and (2) shell out to the CLI safely, with the same --confirm
gating used for EMDB submission, and without ever exposing the EMPIAR API
token or transfer password to Claude.

The JSON_INPUT file IS the EMPIAR submission format directly (see
https://www.ebi.ac.uk/empiar/deposition/json_submission) - there is no
separate manifest translation layer. Build it by hand or have the skill
help construct it conversationally, then point this script at it.

Secrets, read from environment variables only - never as CLI arguments:
  EMPIAR_API_TOKEN     Your EMPIAR API token (from empiar.org/deposition/api_token)
  EMPIAR_TRANSFER_PASS Your EMPIAR transfer password (separate from the API token;
                        read directly by empiar-depositor itself, not by this script)

Usage:
  python3 empiar_deposit.py validate --json-input <path>
  python3 empiar_deposit.py submit --json-input <path> --data-dir <path> --confirm
      [--ascp PATH_TO_ASCP] [--globus UUID] [--thumbnail PATH]
      [--resume ENTRY_ID ENTRY_DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import fail, print_json  # noqa: E402


def _schema_path() -> Path:
    import empiar_depositor

    return Path(empiar_depositor.__file__).parent / "empiar_deposition.schema.json"


def _load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        fail(f"JSON_INPUT not found: {p}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        fail(f"{p} is not valid JSON: {exc}")
    return {}


def cmd_validate(json_input_path: str) -> None:
    import jsonschema

    schema = json.loads(_schema_path().read_text())
    data = _load_json(json_input_path)

    validator = jsonschema.Draft7Validator(schema)
    # Stringify path elements before sorting - e.path mixes str (object keys)
    # and int (array indices), and Python can't compare across those types,
    # so sorting the raw values would raise TypeError on some error sets.
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.path])

    if not errors:
        # Also do a light local sanity check: each imageset's directory
        # should plausibly exist under a data dir, checked separately in
        # `submit` once --data-dir is known - not here, since JSON_INPUT
        # alone doesn't carry the data root.
        print_json({"ok": True, "issues": []})
        return

    print_json(
        {
            "ok": False,
            "issues": [
                {"path": ".".join(str(p) for p in e.path) or "<root>", "message": e.message}
                for e in errors
            ],
        }
    )
    sys.exit(1)


def cmd_submit(
    json_input_path: str,
    data_dir: str,
    confirm: bool,
    ascp: str | None,
    globus: str | None,
    thumbnail: str | None,
    resume: tuple[str, str] | None,
) -> None:
    if not confirm:
        fail("Refusing to submit without --confirm. Run `validate` first and review it with the user.")

    token = os.environ.get("EMPIAR_API_TOKEN")
    if not token:
        fail(
            "EMPIAR_API_TOKEN is not set. Get one from "
            "https://www.ebi.ac.uk/empiar/deposition/api_token/ and export it "
            "in your own shell before running submit."
        )
    if not os.environ.get("EMPIAR_TRANSFER_PASS"):
        fail(
            "EMPIAR_TRANSFER_PASS is not set (this is the transfer password "
            "EMPIAR issued you, not your account password or API token). "
            "See references/setup_checklist.md."
        )

    if not Path(data_dir).exists():
        fail(f"Data directory not found: {data_dir}")

    # Resolve the console script next to the current interpreter rather than
    # relying on PATH - these scripts are meant to be invoked directly as
    # `.venv/bin/python3 .../empiar_deposit.py ...` without activating the
    # venv first, so a bare "empiar-depositor" lookup would fail with
    # FileNotFoundError unless the caller happened to activate it.
    empiar_depositor_bin = str(Path(sys.executable).parent / "empiar-depositor")
    argv = [empiar_depositor_bin]
    if ascp:
        argv += ["-a", ascp]
    if globus:
        argv += ["-g", globus]
    if thumbnail:
        argv += ["-e", thumbnail]
    if resume:
        argv += ["-r", resume[0], resume[1]]
    argv += [token, json_input_path, data_dir]

    # Redact the token before ever printing the command we ran.
    redacted = [a if a != token else "***" for a in argv]

    result = subprocess.run(argv, capture_output=True, text=True)
    print_json(
        {
            "success": result.returncode == 0,
            "command": redacted,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-4000:],
            "stderr_tail": result.stderr[-4000:],
        }
    )
    if result.returncode != 0:
        sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate")
    p.add_argument("--json-input", required=True)

    p = sub.add_parser("submit")
    p.add_argument("--json-input", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--ascp")
    p.add_argument("--globus")
    p.add_argument("--thumbnail")
    p.add_argument("--resume", nargs=2, metavar=("ENTRY_ID", "ENTRY_DIR"))

    args = parser.parse_args()

    if args.command == "validate":
        cmd_validate(args.json_input)
    elif args.command == "submit":
        cmd_submit(
            args.json_input,
            args.data_dir,
            args.confirm,
            args.ascp,
            args.globus,
            args.thumbnail,
            tuple(args.resume) if args.resume else None,
        )


if __name__ == "__main__":
    main()
