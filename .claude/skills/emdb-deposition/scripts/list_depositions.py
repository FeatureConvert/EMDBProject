"""List local depositions and their state at a glance.

Read-only, local-only - no network, no authentication needed. Scans
depositions/<slug>/ for manifest.json (EMDB) and empiar/json_input.json +
empiar/*.submitted.json (EMPIAR), and reports each slug's key state fields
without needing to open and read every file by hand. Useful once a
researcher has more than one or two in-flight depositions.

Usage:
  .venv/bin/python3 .claude/skills/emdb-deposition/scripts/list_depositions.py
      [--depositions-dir <path>]

Default --depositions-dir is "depositions" relative to the current working
directory, matching every other script's assumption that commands run from
the project root.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import JsonArgumentParser, load_optional_json, print_json, run_cli  # noqa: E402


def _emdb_summary(slug_dir: Path) -> dict | None:
    manifest_path = slug_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = load_optional_json(manifest_path, label=f"{manifest_path}")
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


def _empiar_summary(slug_dir: Path) -> dict | None:
    empiar_dir = slug_dir / "empiar"
    if not empiar_dir.is_dir():
        return None
    json_inputs = sorted(empiar_dir.glob("*.json"))
    # Exclude the resubmission marker files (<name>.submitted.json) from
    # the "here's your JSON_INPUT" listing - they're derived state, not
    # inputs themselves.
    json_inputs = [p for p in json_inputs if not p.name.endswith(".submitted.json")]
    if not json_inputs:
        return None

    entries = []
    for json_input in json_inputs:
        marker = json_input.with_suffix(".submitted.json")
        marker_data = load_optional_json(marker, label=f"{marker}")
        entries.append(
            {
                "json_input": json_input.name,
                "state": "submitted" if marker_data.get("entry_id") else "not yet submitted",
                "entry_id": marker_data.get("entry_id"),
                "entry_directory": marker_data.get("entry_directory"),
                "submitted_at": marker_data.get("submitted_at"),
            }
        )
    return {"json_inputs": entries}


def cmd_list(depositions_dir: str) -> None:
    root = Path(depositions_dir)
    if not root.is_dir():
        print_json({"depositions_dir": str(root), "exists": False, "depositions": {}})
        return

    depositions = {}
    for slug_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        emdb = _emdb_summary(slug_dir)
        empiar = _empiar_summary(slug_dir)
        if emdb is None and empiar is None:
            continue  # not a deposition slug (e.g. a stray directory)
        depositions[slug_dir.name] = {"emdb": emdb, "empiar": empiar}

    print_json({"depositions_dir": str(root), "exists": True, "depositions": depositions})


def main() -> None:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--depositions-dir", default="depositions")
    args = parser.parse_args()

    run_cli(lambda: cmd_list(args.depositions_dir))


if __name__ == "__main__":
    main()
