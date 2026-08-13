"""Shared helpers for the emdb-deposition skill scripts.

Manifest files are plain JSON living under depositions/<slug>/manifest.json,
outside this skill package. They accumulate everything needed to drive an
onedep_lib deposition across separate CLI invocations (prepare -> dry-run ->
submit -> status), keyed by a local onedep_lib session_id once one exists.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, default=str))


def fail(message: str, **extra: Any) -> None:
    print_json({"success": False, "error": message, **extra})
    sys.exit(1)


def load_manifest(path: str | Path, label: str = "Manifest") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        fail(f"{label} not found: {p}")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        fail(f"{label} at {p} is not valid JSON: {exc}")
    return {}  # unreachable, keeps type checkers happy


def load_optional_json(path: str | Path, label: str = "File") -> dict[str, Any]:
    """Like load_manifest, but returns {} instead of fail()ing when the file
    doesn't exist yet - for sidecar state (e.g. a resubmission marker) that's
    genuinely optional on a first run, while still giving a clean error (not
    a raw JSONDecodeError traceback) if the file exists but is corrupt."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        fail(f"{label} at {p} is not valid JSON: {exc}")
    return {}  # unreachable, keeps type checkers happy


def require_fields(container: Any, fields: list[str], label: str = "Manifest") -> None:
    """Fail with a clean JSON error if any of `fields` is missing from container,
    or if container isn't even a dict to begin with (e.g. a manifest field that
    should be an object but was written as `null` or a string by mistake).

    Without this, code that indexes container[...] directly raises a raw
    KeyError/TypeError traceback on stderr with no JSON on stdout - breaking
    the "every script prints structured JSON" contract the skill relies on to
    parse results.
    """
    if not isinstance(container, dict):
        fail(f"{label} must be an object with fields {fields}, got {type(container).__name__}: {container!r}")
    missing = [f for f in fields if f not in container]
    if missing:
        fail(f"{label} is missing required field(s): {', '.join(missing)}")


def require_confirm(confirm: bool, review_cmd: str) -> None:
    """Fail with a clean JSON error if --confirm wasn't passed.

    Shared by every subcommand that does something real/hard-to-undo
    (deposit(), a live EMPIAR transfer), so the wording and behavior of this
    safety gate can't drift between scripts.
    """
    if not confirm:
        fail(f"Refusing to submit without --confirm. Run `{review_cmd}` first and review it with the user.")


def guard_resubmission(state: dict[str, Any], id_field: str, force: bool, kind: str) -> None:
    """Fail with a clean JSON error if state[id_field] is already set and
    --force wasn't passed - re-running submit would re-run against a
    deposition/entry that already exists remotely."""
    if state.get(id_field) and not force:
        fail(
            f"This {kind} already has {id_field}={state[id_field]!r}. "
            f"Re-running submit will re-run against the existing {kind}. "
            "Pass --force if that's intentional."
        )


def require_submission_safety_gates(
    confirm: bool, review_cmd: str, state: dict[str, Any], id_field: str, force: bool, kind: str
) -> None:
    """Combine require_confirm() and guard_resubmission() - every current
    hard-to-undo action in this project needs both, always in this order.
    A future script copying the submit pattern by eye is one dropped call
    away from silently permitting an unconfirmed or duplicate submission;
    calling this one function instead removes that chance."""
    require_confirm(confirm, review_cmd)
    guard_resubmission(state, id_field, force, kind)


def run_cli(dispatch) -> None:
    """Run `dispatch()` (a zero-arg callable that executes the selected
    subcommand), converting ANY uncaught exception into the same JSON error
    contract every explicit fail() call already uses, instead of a raw
    traceback on stderr with no parseable JSON on stdout.

    Deliberately catches bare Exception rather than an enumerated list of
    "expected" exception types: onedep_lib, jsonschema, and empiar_depositor
    can each raise types this project doesn't control (ConfigError,
    UnicodeDecodeError, TypeError from a malformed manifest field, ...), and
    a previous fix that enumerated specific types kept missing new ones. This
    intentionally does NOT catch SystemExit (raised by fail() itself via
    sys.exit(), and by argparse) or KeyboardInterrupt - both are
    BaseException subclasses, not Exception, so existing fail() call sites
    and Ctrl-C still behave normally.
    """
    try:
        dispatch()
    except Exception as exc:  # noqa: BLE001 - see docstring for why this is deliberately broad
        fail(f"{type(exc).__name__}: {exc}")


class JsonArgumentParser(argparse.ArgumentParser):
    """An ArgumentParser whose usage errors (missing/invalid arguments,
    unknown subcommand) go through fail()'s JSON contract instead of
    argparse's default behavior - printing a plain-text usage message to
    stderr and exit(2). Found by actually running the scripts with bad
    arguments: parser.parse_args() runs before run_cli()'s dispatch, so
    without this, a CLI usage error was the one failure mode that still
    produced no JSON on stdout. Subparsers created via add_subparsers()
    automatically inherit this class, so this only needs to be used for
    each script's top-level parser."""

    def error(self, message: str) -> None:
        fail(f"Argument error: {message}")


def save_manifest(path: str | Path, data: dict[str, Any]) -> None:
    """Write JSON atomically (write to a temp file, then os.replace()) - the
    same pattern onedep_lib's own JsonSessionStore._save() uses, so a crash
    or kill mid-write leaves the previous good version intact instead of a
    truncated file that fails to parse on the next read."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, p)


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

    # Normalize spaces/hyphens the same way country_enum() does - "single
    # particle"/"single-particle" are natural phrasings (this project's own
    # docs describe subtypes as "SPA / helical / subtomogram / tomography"
    # in prose), so they should resolve the same as the underscored form.
    key = name.strip().upper().replace(" ", "_").replace("-", "_")
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
