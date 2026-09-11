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

Note on --ascp/--globus: empiar-depositor's own CLI already refuses to run
at all (prints a message, exits 1, before creating anything) if neither is
given - confirmed by reading its installed source. It does NOT, however,
auto-detect an installed Aspera Connect the way its --help text might
suggest, so this script does that extra step itself (see
_default_ascp_path()) and requires --ascp/--globus to resolve before it
will even shell out - purely to fail faster with a clearer message and
without spawning a subprocess, not because skipping it would create a
live entry with no data (it wouldn't - the wrapped CLI's own guard already
prevents that).

Usage:
  python3 empiar_deposit.py validate --json-input <path>
  python3 empiar_deposit.py preview  --json-input <path> [--data-dir <path>]
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
    SUBMITTED_MARKER_SUFFIX,
    JsonArgumentParser,
    fail,
    iter_schema_issues,
    load_manifest,
    load_optional_json,
    print_json,
    require_confirm,
    require_submission_safety_gates,
    run_cli,
    save_manifest,
    save_text,
    submitted_marker_path,
)

_ENTRY_ID_RE = re.compile(r"Your entry ID is (\d+) and unique data directory is (\S+)")


def _schema_path() -> Path:
    import empiar_depositor

    return Path(empiar_depositor.__file__).parent / "empiar_deposition.schema.json"


def _reject_marker_named_input(json_input_path: str) -> None:
    """A JSON_INPUT deliberately named *.submitted.json would collide with
    the submission-marker sidecar scheme (common.submitted_marker_path) and
    be misread as a marker by list_depositions.py - refuse it up front,
    before any validation or submission work."""
    if Path(json_input_path).name.endswith(SUBMITTED_MARKER_SUFFIX):
        fail(
            f"JSON_INPUT path must not end with {SUBMITTED_MARKER_SUFFIX!r} - that "
            "suffix is reserved for the submission-marker sidecar files this "
            "script writes after a successful submit. Rename the file and retry."
        )


def _validate(json_input_path: str) -> tuple[bool, list[dict]]:
    """Schema-validate a JSON_INPUT file. Shared by cmd_validate and
    cmd_submit, so submit re-checks against the current file on disk rather
    than trusting that a validate run earlier in the conversation still
    reflects any edits made since."""
    schema = json.loads(_schema_path().read_text())
    data = load_manifest(json_input_path, label="JSON_INPUT")
    issues = iter_schema_issues(data, schema)
    return not issues, issues


def cmd_validate(json_input_path: str) -> None:
    # Also worth a light local sanity check beyond schema validation: each
    # imageset's directory should plausibly exist under a data dir - not
    # checked here since JSON_INPUT alone doesn't carry the data root, but
    # cmd_submit checks --data-dir itself once it's known.
    _reject_marker_named_input(json_input_path)
    ok, issues = _validate(json_input_path)
    print_json({"ok": ok, "issues": issues})
    if not ok:
        sys.exit(1)


def _render_preview(json_input_path: str, data_dir: str | None, ok: bool, issues: list[dict]) -> str:
    """Render what `submit` will send to EMPIAR as human-readable Markdown.
    The JSON_INPUT file IS the submission payload, so this shows it verbatim
    (pretty-printed) plus the schema-validation result and each imageset's
    referenced directory - resolved under --data-dir when given, so the
    depositor can confirm the data is where EMPIAR will look for it."""
    data = load_manifest(json_input_path, label="JSON_INPUT")

    lines: list[str] = []
    lines.append("# EMPIAR deposition preview")
    lines.append("")
    lines.append(f"JSON_INPUT: `{json_input_path}`")
    lines.append("")
    lines.append(
        "This is a **read-only preview** of what `submit` will send to EMPIAR. "
        "Nothing has been submitted or transferred. Review it, then run "
        "`submit --confirm` when you're ready."
    )
    lines.append("")

    lines.append(f"## Schema validation: {'PASSED' if ok else 'FAILED'}")
    lines.append("")
    if issues:
        for issue in issues:
            lines.append(f"- `{issue['path']}`: {issue['message']}")
    else:
        lines.append("No schema issues.")
    lines.append("")

    title = data.get("title")
    if title:
        lines.append(f"**Title:** {title}")
        lines.append("")

    imagesets = data.get("imagesets")
    if isinstance(imagesets, list) and imagesets:
        lines.append(f"## Imagesets ({len(imagesets)})")
        lines.append("")
        for i, imgset in enumerate(imagesets):
            name = imgset.get("name") if isinstance(imgset, dict) else None
            directory = imgset.get("directory") if isinstance(imgset, dict) else None
            lines.append(f"- **{name or f'imageset {i}'}**")
            if directory is not None:
                note = ""
                if data_dir is not None:
                    resolved = Path(data_dir) / str(directory).lstrip("/")
                    note = "  — found" if resolved.exists() else "  — **NOT found under --data-dir**"
                lines.append(f"  - directory: `{directory}`{note}")
        lines.append("")

    lines.append("## Full JSON_INPUT payload")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(data, indent=2))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def cmd_preview(json_input_path: str, data_dir: str | None) -> None:
    _reject_marker_named_input(json_input_path)
    ok, issues = _validate(json_input_path)
    preview = _render_preview(json_input_path, data_dir, ok, issues)
    preview_path = Path(json_input_path).with_suffix(".preview.md")
    save_text(preview_path, preview)
    print_json(
        {
            "success": True,
            "schema_ok": ok,
            "preview_path": str(preview_path),
            "preview_markdown": preview,
            "note": (
                "Read-only preview written. Nothing submitted or transferred. "
                "Show this to the depositor, then run submit --confirm when ready."
            ),
        }
    )


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
    _reject_marker_named_input(json_input_path)
    marker_path = submitted_marker_path(json_input_path)
    if resume:
        # --resume is specifically for continuing an interrupted transfer
        # against the SAME entry the marker (if any) already recorded -
        # that's not a duplicate submission, it's the documented recovery
        # path, so the guard doesn't apply here. --force keeps its own
        # documented meaning ("deliberately create a separate entry"),
        # which would be the wrong thing to require for a resume.
        require_confirm(confirm, "validate")
    else:
        prior = load_optional_json(marker_path, label="Submission marker")
        require_submission_safety_gates(confirm, "validate", prior, "entry_id", force, kind="EMPIAR entry")

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
            "install location) nor --globus was given. empiar-depositor's "
            "own CLI would also refuse to run without one of these - this "
            "check just fails faster with a clearer message, before "
            "spawning a subprocess. Install Aspera Connect or globus-cli, "
            "or pass --ascp/--globus explicitly, before retrying."
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
    warning = None
    match = _ENTRY_ID_RE.search(result.stdout)
    if match:
        entry_id, entry_directory = match.group(1), match.group(2)
    elif result.returncode == 0:
        # returncode 0 but we couldn't parse an entry_id (empiar-depositor
        # changed its stdout wording, or something equally unexpected) -
        # still write a marker so a resubmission requires --force. Without
        # this, guard_resubmission would see no marker next time and wave
        # a retry straight through, creating a SECOND live EMPIAR entry -
        # exactly what the guard exists to prevent, silently defeated by a
        # parsing gap rather than an actual duplicate-submission attempt.
        entry_id = f"unparsed-success-{datetime.now(tz=timezone.utc).isoformat()}"
        warning = (
            "submit reported success but the entry ID could not be parsed "
            "from empiar-depositor's output - wrote a placeholder marker so "
            "a resubmission still requires --force. Check stdout_tail and "
            "your EMPIAR account manually to find the real entry before "
            "retrying."
        )

    if result.returncode == 0:
        # The live EMPIAR entry was already created by this point - a
        # failure persisting the marker must not suppress reporting
        # entry_id/entry_directory, which the user needs regardless.
        try:
            save_manifest(
                marker_path,
                {
                    "entry_id": entry_id,
                    "entry_directory": entry_directory,
                    "returncode": result.returncode,
                    "submitted_at": datetime.now(tz=timezone.utc).isoformat(),
                },
            )
        except Exception:  # noqa: BLE001
            warning = (
                (warning + " " if warning else "")
                + "Additionally, failed to write the resubmission-guard marker file - "
                "a retry will NOT be blocked automatically; check manually before resubmitting."
            )

    print_json(
        {
            "success": result.returncode == 0,
            "entry_id": entry_id,
            "entry_directory": entry_directory,
            "warning": warning,
            "command": redacted,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-4000:],
            "stderr_tail": result.stderr[-4000:],
        }
    )
    if result.returncode != 0:
        sys.exit(result.returncode)


def main() -> None:
    parser = JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    # Handlers wired via set_defaults(func=...) - see em_deposit.main() for
    # why this beats a hand-mirrored if/elif dispatch chain.
    p = sub.add_parser("validate")
    p.add_argument("--json-input", required=True)
    p.set_defaults(func=lambda args: cmd_validate(args.json_input))

    p = sub.add_parser("preview")
    p.add_argument("--json-input", required=True)
    p.add_argument("--data-dir", help="optional: resolve each imageset's directory under this root")
    p.set_defaults(func=lambda args: cmd_preview(args.json_input, args.data_dir))

    p = sub.add_parser("submit")
    p.add_argument("--json-input", required=True)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--ascp")
    p.add_argument("--globus")
    p.add_argument("--thumbnail")
    p.add_argument("--resume", nargs=2, metavar=("ENTRY_ID", "ENTRY_DIR"))
    p.set_defaults(
        func=lambda args: cmd_submit(
            args.json_input,
            args.data_dir,
            args.confirm,
            args.force,
            args.ascp,
            args.globus,
            args.thumbnail,
            tuple(args.resume) if args.resume else None,
        )
    )

    args = parser.parse_args()
    run_cli(lambda: args.func(args))


if __name__ == "__main__":
    main()
