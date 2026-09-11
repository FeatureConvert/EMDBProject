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
import re
import sys
from pathlib import Path
from typing import Any, NoReturn


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, default=str))


def fail(message: str, **extra: Any) -> NoReturn:
    print_json({"success": False, "error": message, **extra})
    sys.exit(1)


def _require_json_object(data: Any, label: str, p: Path) -> dict[str, Any]:
    """json.loads happily returns lists/strings/numbers for valid JSON that
    isn't an object; callers of the loaders below all .get()/index the result,
    so anything non-dict must fail here with the file named, not later with a
    generic AttributeError that names nothing."""
    if not isinstance(data, dict):
        fail(f"{label} at {p} must contain a JSON object, got {type(data).__name__}")
    return data


def load_manifest(path: str | Path, label: str = "Manifest") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        fail(f"{label} not found: {p}")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        fail(f"{label} at {p} is not valid JSON: {exc}")
    return _require_json_object(data, label, p)


def load_optional_json(path: str | Path, label: str = "File") -> dict[str, Any]:
    """Like load_manifest, but returns {} instead of fail()ing when the file
    doesn't exist yet - for sidecar state (e.g. a resubmission marker) that's
    genuinely optional on a first run, while still giving a clean error (not
    a raw JSONDecodeError traceback) if the file exists but is corrupt."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        fail(f"{label} at {p} is not valid JSON: {exc}")
    return _require_json_object(data, label, p)


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


def iter_schema_issues(data: Any, schema: dict) -> list[dict]:
    """Validate `data` against a Draft-7 JSON Schema, returning a sorted list
    of {path, message} issues ([] when valid). Shared by the EMDB manifest
    check and the EMPIAR JSON_INPUT check so both report schema problems the
    same way."""
    import jsonschema

    validator = jsonschema.Draft7Validator(schema)
    # Stringify path elements before sorting - e.path mixes str (object keys)
    # and int (array indices), and Python can't compare across those types,
    # so sorting the raw values would raise TypeError on some error sets.
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.path])
    return [{"path": ".".join(str(p) for p in e.path) or "<root>", "message": e.message} for e in errors]


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

    def error(self, message: str) -> NoReturn:
        # NoReturn is load-bearing, not just documentation: argparse code
        # calling self.error() assumes it never returns and would continue
        # into undefined behavior (e.g. returning an unbound namespace) if
        # it did. fail() always exits, satisfying that.
        fail(f"Argument error: {message}")


def save_manifest(path: str | Path, data: dict[str, Any]) -> None:
    """Write JSON atomically (write to a temp file, then os.replace()) - the
    same pattern onedep_lib's own JsonSessionStore._save() uses, so a crash
    or kill mid-write leaves the previous good version intact instead of a
    truncated file that fails to parse on the next read."""
    _atomic_write(path, json.dumps(data, indent=2, default=str))


def save_text(path: str | Path, text: str) -> None:
    """Atomically write a plain-text file (e.g. a human-readable submission
    preview), same crash-safety as save_manifest."""
    _atomic_write(path, text)


def _atomic_write(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, p)


def short_repr(value: Any, limit: int = 200) -> str:
    """repr() capped for error messages - a wrong-typed value can be
    arbitrarily large (a whole misplaced object), and dumping it wholesale
    into the JSON error would bury the message."""
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def require_type(value: Any, expected: type, label: str, expected_desc: str) -> Any:
    """Fail with a clean, specific message instead of a generic
    AttributeError/TypeError further down, if a manifest field has the
    wrong JSON type. One definition so the wording can't drift between
    call sites."""
    if not isinstance(value, expected):
        fail(f"{label} must be {expected_desc}, got {type(value).__name__}: {short_repr(value)}")
    return value


def require_str(value: Any, label: str) -> str:
    """require_type specialized for the common case: a manifest field that
    should be a string is a number, null, or something else."""
    return require_type(value, str, label, "a string")


# --- lightweight format checks (ORCID, email) --------------------------------
# These catch obvious mistakes locally (before wwPDB does) without pretending
# to be exhaustive validators. Both return a problem message or None, so the
# caller controls how to report - matching the _sniff_mrc() pattern.

_ORCID_RE = re.compile(r"^(\d{4})-(\d{4})-(\d{4})-(\d{3}[\dX])$")
# Deliberately loose: exactly one @, no spaces, and a dot in the domain.
# A stricter regex risks rejecting valid addresses, and the real check is
# wwPDB's anyway - this only catches fat-finger mistakes like a missing @.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def orcid_problem(value: str) -> str | None:
    """Return a message if `value` isn't a structurally valid ORCID iD, else
    None. Validates the 16-digit 0000-0002-1825-0097 form and the ISO 7064
    MOD 11-2 checksum (final char may be X). Tolerates an https://orcid.org/
    prefix. Any string is otherwise accepted upstream, so a transposed or
    truncated iD would reach wwPDB unnoticed without this."""
    raw = value.strip()
    for prefix in ("https://orcid.org/", "http://orcid.org/", "orcid.org/"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            break
    if not _ORCID_RE.match(raw):
        return (
            f"{value!r} is not a valid ORCID iD - expected 16 digits grouped as "
            "0000-0002-1825-0097 (the final character may be X)"
        )
    digits = raw.replace("-", "")
    total = 0
    for ch in digits[:15]:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    expected = "X" if check == 10 else str(check)
    if expected != digits[15]:
        return f"{value!r} has an invalid ORCID checksum digit (computed {expected}, found {digits[15]})"
    return None


def email_problem(value: str) -> str | None:
    """Return a message if `value` clearly isn't an email address, else None.
    Deliberately permissive - see _EMAIL_RE."""
    if not _EMAIL_RE.match(value.strip()):
        return f"{value!r} doesn't look like an email address (expected something like name@example.org)"
    return None


_EMDB_ACCESSION_RE = re.compile(r"^EMD-\d{4,}$")


def emdb_accession_problem(value: str) -> str | None:
    """Return a message if `value` isn't a well-formed EMDB accession
    (EMD-XXXX, four or more digits), else None. Format only - this does not
    check the entry exists (that needs a network lookup; see emdb_lookup.py)."""
    if not isinstance(value, str) or not _EMDB_ACCESSION_RE.match(value.strip()):
        return f"{value!r} is not a valid EMDB accession - expected EMD-XXXX (e.g. EMD-8000)"
    return None


# --- EMPIAR submission-marker sidecar ----------------------------------------


SUBMITTED_MARKER_SUFFIX = ".submitted.json"


def submitted_marker_path(json_input_path: str | Path) -> Path:
    """Sidecar file recording a successful prior EMPIAR submission - for
    `json_input.json` the marker is `json_input.submitted.json` (the `.json`
    suffix is replaced, not appended to). empiar-depositor doesn't give us a
    manifest-like state file of our own to persist an entry_id into, unlike
    em_deposit.py's manifest.json. Defined here in one place because both
    the writer (empiar_deposit.py) and the reader (list_depositions.py)
    depend on the exact same scheme - a divergence would make submitted
    entries silently report as never submitted."""
    return Path(json_input_path).with_suffix(SUBMITTED_MARKER_SUFFIX)


# --- enum name -> onedep_lib enum member lookups -----------------------------
# Manifests store plain strings (JSON-friendly); these map them to the real
# enums onedep_lib expects. Keys are case-insensitive.


def country_enum(name: str):
    import onedep_lib as dsp

    name = require_str(name, "country")
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


def experiment_type_enum(name: str):
    import onedep_lib as dsp

    name = require_str(name, "experiment_type")
    key = name.strip().upper().replace(" ", "_").replace("-", "_")
    try:
        return dsp.ExperimentType[key]
    except KeyError:
        fail(
            f"Unknown experiment_type: {name!r}. Expected one of: "
            + ", ".join(t.name for t in dsp.ExperimentType)
        )


def em_subtype_enum(name: str):
    import onedep_lib as dsp

    name = require_str(name, "em_subtype")
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

    name = require_str(name, "file_type")
    key = name.strip().upper()
    try:
        return dsp.FileType[key]
    except KeyError:
        fail(
            f"Unknown file type: {name!r}. Expected one of: "
            + ", ".join(t.name for t in dsp.FileType)
        )
