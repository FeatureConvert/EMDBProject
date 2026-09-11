"""List local depositions and their state at a glance.

Read-only, local-only - no network, no authentication needed. Scans
depositions/<slug>/ for manifest.json (EMDB) and empiar/json_input.json +
empiar/<name>.submitted.json (EMPIAR), and reports each slug's key state
fields without needing to open and read every file by hand. Useful once a
researcher has more than one or two in-flight depositions.

Because this is an overview tool, a single corrupt or unreadable file in
one slug is reported as that slug's own error rather than aborting the whole
listing - the other, healthy depositions still show. Process-level failure
(fail()) is reserved for a problem with the listing itself (e.g. the
depositions dir isn't a directory).

Usage:
  .venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/list_depositions.py
      [--depositions-dir <path>]

Default --depositions-dir is "depositions" relative to the current working
directory, matching every other script's assumption that commands run from
the project root.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (  # noqa: E402
    SUBMITTED_MARKER_SUFFIX,
    JsonArgumentParser,
    print_json,
    run_cli,
    submitted_marker_path,
)


def _read_json_object(path: Path) -> dict:
    """Read one local JSON file that must be an object, raising ValueError
    (never exiting) on any problem so the caller can attach it to a single
    slug instead of aborting the whole listing. Distinct from common's
    load_* helpers, which fail() the whole process - the wrong behavior for
    a many-slug overview."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        # OSError covers IsADirectoryError (a directory named manifest.json)
        # and unreadable files; UnicodeError covers non-UTF-8 bytes (e.g. a
        # macOS AppleDouble binary sibling).
        raise ValueError(f"{path.name} could not be read: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object, got {type(data).__name__}")
    return data


def _is_marker(path: Path) -> bool:
    return path.name.endswith(SUBMITTED_MARKER_SUFFIX)


def _hidden(path: Path) -> bool:
    # pathlib's glob("*.json") matches dotfiles (unlike glob.glob), so a
    # macOS AppleDouble sidecar like ._json_input.json would otherwise show
    # as a phantom JSON_INPUT and its binary marker sibling could abort the
    # read. Deposition inputs are never dotfiles.
    return path.name.startswith(".")


def _emdb_summary(slug_dir: Path) -> dict | None:
    manifest_path = slug_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = _read_json_object(manifest_path)
    except ValueError as exc:
        return {"state": "error", "error": str(exc)}
    if manifest.get("remote_dep_id"):
        state = "submitted"
    elif manifest.get("session_id"):
        state = "prepared"
    else:
        state = "manifest only (prepare not yet run)"
    return {
        "state": state,
        "session_id": manifest.get("session_id"),
        "remote_dep_id": manifest.get("remote_dep_id"),
        "site_url": manifest.get("site_url"),
    }


def _empiar_entry(json_input: Path | None, marker: Path) -> dict:
    """Summarize one EMPIAR submission from its marker (always) and its
    JSON_INPUT file (when it still exists). Reads the marker even when the
    input was renamed/deleted, so a submitted entry never vanishes."""
    entry: dict = {"json_input": json_input.name if json_input else None}
    try:
        marker_data = _read_json_object(marker) if marker.exists() else {}
    except ValueError as exc:
        return {**entry, "state": "error", "error": str(exc)}
    entry["state"] = "submitted" if marker_data.get("entry_id") else "not yet submitted"
    entry["entry_id"] = marker_data.get("entry_id")
    entry["entry_directory"] = marker_data.get("entry_directory")
    entry["submitted_at"] = marker_data.get("submitted_at")
    if json_input is None:
        # A marker with no surviving input - still a real submitted entry.
        entry["note"] = "JSON_INPUT file no longer present; state from submission marker."
    return entry


def _empiar_summary(slug_dir: Path) -> dict | None:
    empiar_dir = slug_dir / "empiar"
    if not empiar_dir.is_dir():
        return None

    all_json = [p for p in empiar_dir.glob("*.json") if not _hidden(p)]
    inputs = sorted(p for p in all_json if not _is_marker(p))
    markers = {p for p in all_json if _is_marker(p)}

    entries = []
    # Every JSON_INPUT, paired with its marker (present or not).
    for json_input in inputs:
        marker = submitted_marker_path(json_input)
        markers.discard(marker)
        entries.append(_empiar_entry(json_input, marker))
    # Orphan markers (input deleted, renamed, or was extension-less so it was
    # never globbed) - a submitted entry must not disappear from the overview.
    for marker in sorted(markers):
        entries.append(_empiar_entry(None, marker))

    if not entries:
        return None
    return {"json_inputs": entries}


def cmd_list(depositions_dir: str) -> None:
    root = Path(depositions_dir)
    if not root.exists():
        # A legitimate empty state, not an error: a fresh project has no
        # depositions/ dir until the first deposition is created. Exit 0.
        print_json({"depositions_dir": str(root), "exists": False, "depositions": {}})
        return
    if not root.is_dir():
        # Exists but isn't a directory - a real problem with the listing
        # target itself (e.g. a file where a dir was expected, or a typo'd
        # --depositions-dir pointing at a file), distinct from "not there".
        print_json(
            {
                "success": False,
                "error": f"depositions_dir exists but is not a directory: {root}",
                "depositions_dir": str(root),
            }
        )
        sys.exit(1)

    depositions = {}
    for slug_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        emdb = _emdb_summary(slug_dir)
        empiar = _empiar_summary(slug_dir)
        if emdb is None and empiar is None:
            continue  # not a deposition slug (e.g. a stray directory)
        depositions[slug_dir.name] = {"emdb": emdb, "empiar": empiar}

    print_json({"depositions_dir": str(root), "exists": True, "depositions": depositions})


def main() -> None:
    parser = JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--depositions-dir", default="depositions")
    args = parser.parse_args()

    run_cli(lambda: cmd_list(args.depositions_dir))


if __name__ == "__main__":
    main()
