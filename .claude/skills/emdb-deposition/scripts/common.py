"""Shared helpers for the emdb-deposition skill scripts.

Manifest files are plain JSON living under depositions/<slug>/manifest.json,
outside this skill package. They accumulate everything needed to drive an
onedep_lib deposition across separate CLI invocations (prepare -> dry-run ->
submit -> status), keyed by a local onedep_lib session_id once one exists.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, default=str))


def fail(message: str, **extra: Any) -> None:
    print_json({"success": False, "error": message, **extra})
    sys.exit(1)


def load_manifest(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        fail(f"Manifest not found: {p}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        fail(f"Manifest at {p} is not valid JSON: {exc}")
    return {}  # unreachable, keeps type checkers happy


def require_fields(manifest: dict[str, Any], fields: list[str]) -> None:
    """Fail with a clean JSON error if any of `fields` is missing from manifest.

    Without this, code that indexes manifest[...] directly raises a raw
    KeyError traceback on stderr with no JSON on stdout - breaking the
    "every script prints structured JSON" contract the skill relies on to
    parse results.
    """
    missing = [f for f in fields if f not in manifest]
    if missing:
        fail(f"Manifest is missing required field(s): {', '.join(missing)}")


def save_manifest(path: str | Path, data: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, default=str))


# --- enum name -> onedep_lib enum member lookups -----------------------------
# Manifests store plain strings (JSON-friendly); these map them to the real
# enums onedep_lib expects. Keys are case-insensitive.


def country_enum(name: str):
    import onedep_lib as dsp

    key = name.strip().upper().replace(" ", "_").replace("-", "_")
    try:
        return dsp.Country[key]
    except KeyError:
        # Fall back to matching by human-readable value, e.g. "United Kingdom"
        for member in dsp.Country:
            if member.value.lower() == name.strip().lower():
                return member
        fail(
            f"Unknown country: {name!r}. Use a Country enum name (e.g. UK, USA) "
            "or its exact wwPDB display value (e.g. 'United Kingdom')."
        )


def em_subtype_enum(name: str):
    import onedep_lib as dsp

    key = name.strip().upper()
    aliases = {"SINGLE_PARTICLE": "SPA", "SPA": "SPA"}
    key = aliases.get(key, key)
    try:
        return dsp.EMSubType[key]
    except KeyError:
        fail(
            f"Unknown EM subtype: {name!r}. Expected one of: "
            "SPA, HELICAL, SUBTOMOGRAM, TOMOGRAPHY."
        )


def file_type_enum(name: str):
    import onedep_lib as dsp

    key = name.strip().upper()
    try:
        return dsp.FileType[key]
    except KeyError:
        fail(
            f"Unknown file type: {name!r}. Expected one of: "
            + ", ".join(t.name for t in dsp.FileType)
        )
