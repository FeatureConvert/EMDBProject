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

Secrets, read from environment variables only - this script never accepts
them as its own CLI arguments and never prints the API token:
  EMPIAR_API_TOKEN     Your EMPIAR API token (from empiar.org/deposition/api_token)
  EMPIAR_TRANSFER_PASS Your EMPIAR transfer password (separate from the API token;
                        read directly by empiar-depositor itself, not by this script)

Note: empiar-depositor's own CLI takes the API token as a required
positional argument, so this script necessarily puts it on the argv of the
subprocess it launches - it is redacted from the JSON this script prints
afterward, but it is briefly visible to process-listing tools (`ps`, /proc)
for that subprocess's lifetime. That's a limitation of the wrapped CLI, not
something this wrapper can avoid.

IMPORTANT: empiar-depositor creates the live EMPIAR entry via its API
(create_new_deposition) BEFORE attempting any data transfer, and only
transfers data if --ascp resolves to a real ascp binary or --globus is
given - it does NOT auto-detect an installed Aspera Connect the way its
own --help text might suggest. Omit both and you get a real, empty EMPIAR
entry with no data uploaded, no clear error pointing at the actual cause.
This script therefore requires that --ascp resolves (explicitly or via a
best-effort default-location probe) or --globus is given, before it will
shell out at all - see _resolve_ascp().

Usage:
  python3 empiar_deposit.py validate --json-input <path>
  python3 empiar_deposit.py submit --json-input <path> --data-dir <path> --confirm
      [--ascp PATH_TO_ASCP] [--globus UUID] [--thumbnail PATH]
      [--resume ENTRY_ID ENTRY_DIR] [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (  # noqa: E402
    fail,
    guard_resubmission,
    load_manifest,
    print_json,
    require_confirm,
    run_cli,
)

_ENTRY_ID_RE = re.compile(r"Your entry ID is (\d+) and unique data directory is (\S+)")


def _schema_path() -> Path:
    import empiar_depositor

    return Path(empiar_depositor.__file__).parent / "empiar_deposition.schema.json"


def _submitted_marker_path(json_input_path: str) -> Path:
    """Sidecar file recording a successful prior submission - empiar-depositor
    doesn't give us a manifest-like state file of our own to persist an
    entry_id into, unlike em_deposit.py's manifest.json."""
    return Path(json_input_path).with_suffix(".submitted.json")


def _validate(json_input_path: str) -> tuple[bool, list[dict]]:
    """Schema-validate a JSON_INPUT file. Shared by cmd_validate and
    cmd_submit, so submit re-checks against the current file on disk rather
    than trusting that a validate run earlier in the conversation still
    reflects any edits made since."""
    import jsonschema

    schema = json.loads(_schema_path().read_text())
    data = load_manifest(json_input_path, label="JSON_INPUT")

    validator = jsonschema.Draft7Validator(schema)
    # Stringify path elements before sorting - e.path mixes str (object keys)
    # and int (array indices), and Python can't compare across those types,
    # so sorting the raw values would raise TypeError on some error sets.
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.path])
    issues = [
        {"path": ".".join(str(p) for p in e.path) or "<root>", "message": e.message} for e in errors
    ]
    return not errors, issues


def cmd_validate(json_input_path: str) -> None:
    # Also worth a light local sanity check beyond schema validation: each
    # imageset's directory should plausibly exist under a data dir - not
    # checked here since JSON_INPUT alone doesn't carry the data root, but
    # cmd_submit checks --data-dir itself once it's known.
    ok, issues = _validate(json_input_path)
    print_json({"ok": ok, "issues": issues})
    if not ok:
        sys.exit(1)


def _default_ascp_path() -> str | None:
    """Best-effort default Aspera Connect ascp location, matching
    empiar-depositor's own documented per-platform defaults (its --help text
    describes these paths but the tool itself does not probe them)."""
    system = platform.system()
    if system == "Darwin":
        candidate = Path.home() / "Applications" / "Aspera Connect.app" / "Contents" / "Resources" / "ascp"
    elif system == "Windows":
        candidate = (
            Path.home() / "AppData" / "Local" / "Programs" / "Aspera" / "Aspera Connect" / "bin" / "ascp.exe"
        )
    else:
        candidate = Path.home() / ".aspera" / "connect" / "bin" / "ascp"
    return str(candidate) if candidate.exists() else None


def cmd_submit(
    json_input_path: str,
    data_dir: str,
    confirm: bool,
    force: bool,
    ascp: str | None,
    globus: str | None,
    thumbnail: str | None,
    resume: tuple[str, str] | None,
) -> None:
    require_confirm(confirm, "validate")

    marker_path = _submitted_marker_path(json_input_path)
    prior = json.loads(marker_path.read_text()) if marker_path.exists() else {}
    guard_resubmission(prior, "entry_id", force, kind="EMPIAR entry")

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

    if not ascp:
        ascp = _default_ascp_path()
    if not ascp and not globus:
        fail(
            "Neither --ascp resolved (no Aspera Connect found at the default "
            "install location) nor --globus was given. empiar-depositor "
            "creates the live EMPIAR entry via its API BEFORE attempting any "
            "data transfer, and silently skips the transfer entirely if "
            "neither is available - this would create a real, empty EMPIAR "
            "entry with no data uploaded. Install Aspera Connect or "
            "globus-cli, or pass --ascp/--globus explicitly, before retrying."
        )

    # Re-validate right before shelling out, in case the file was edited
    # since an earlier `validate` call in this conversation.
    ok, issues = _validate(json_input_path)
    if not ok:
        fail("JSON_INPUT failed schema validation - not submitting.", issues=issues)

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

    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        fail(
            f"empiar-depositor executable not found at {empiar_depositor_bin!r}. "
            "Re-run `.venv/bin/pip install -r .claude/skills/emdb-deposition/requirements.txt` "
            "to reinstall it."
        )
        return

    entry_id = entry_directory = None
    match = _ENTRY_ID_RE.search(result.stdout)
    if match:
        entry_id, entry_directory = match.group(1), match.group(2)
        marker_path.write_text(
            json.dumps(
                {
                    "entry_id": entry_id,
                    "entry_directory": entry_directory,
                    "returncode": result.returncode,
                    "submitted_at": datetime.now(tz=timezone.utc).isoformat(),
                },
                indent=2,
            )
        )

    print_json(
        {
            "success": result.returncode == 0,
            "entry_id": entry_id,
            "entry_directory": entry_directory,
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
    p.add_argument("--force", action="store_true")
    p.add_argument("--ascp")
    p.add_argument("--globus")
    p.add_argument("--thumbnail")
    p.add_argument("--resume", nargs=2, metavar=("ENTRY_ID", "ENTRY_DIR"))

    args = parser.parse_args()

    def dispatch() -> None:
        if args.command == "validate":
            cmd_validate(args.json_input)
        elif args.command == "submit":
            cmd_submit(
                args.json_input,
                args.data_dir,
                args.confirm,
                args.force,
                args.ascp,
                args.globus,
                args.thumbnail,
                tuple(args.resume) if args.resume else None,
            )

    run_cli(dispatch)


if __name__ == "__main__":
    main()
