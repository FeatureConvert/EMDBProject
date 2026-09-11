"""Drive an EMDB (EM map) deposition through onedep_lib, keyed by a manifest.

IMPORTANT SCOPE NOTE: onedep_lib's Deposition object only covers experiment
type, EM subtype, file upload (map/half-maps/image/optional coordinates),
and per-map voxel spacing + contour level. It has no methods for the five
detailed experimental sections OneDep's web wizard normally collects
(Specimen Preparation, Microscopy, Image Recording, Reconstruction, Fitting
/Interpretation). After `submit`, the depositor will likely still need to
log into the OneDep web UI to complete those sections before the entry can
be validated/released. This script automates deposition creation and core
file upload only - not full end-to-end submission.

Manifest schema (JSON, created/updated in place by this script; the
authoritative structural definition is scripts/manifest.schema.json):
{
  "email": "depositor@example.org",
  "users": ["0000-0002-XXXX-XXXX"],   # ORCID iD(s), format + checksum validated
  "country": "UK",
  "experiment_type": "EM",            # optional, default EM; also XRAY/NMR/SSNMR/NEUTRON/FIBER/EC
  "em_subtype": "SPA",                # required for EM only: SPA | HELICAL | SUBTOMOGRAM | TOMOGRAPHY
  "coordinates": false,               # true if a coordinates file is included
  "related_emdb": [],                 # optional EMD-XXXXX accessions to cross-reference (link in web UI)
  "session_id": null,                 # filled in by `prepare`
  "remote_dep_id": null,              # filled in by `submit`
  "files": [
    {"path": "/abs/path/map.mrc", "file_type": "EM_MAP",
     "voxel": {"spacing_x": 1.05, "spacing_y": 1.05, "spacing_z": 1.05, "contour": 0.02}},
    {"path": "/abs/path/half_map_1.mrc", "file_type": "EM_HALF_MAP"},
    {"path": "/abs/path/half_map_2.mrc", "file_type": "EM_HALF_MAP"},
    {"path": "/abs/path/preview.png", "file_type": "ENTRY_IMAGE"}
  ]
}
(For non-EM experiment types, omit em_subtype/voxel and register the file
types that method needs - e.g. XRAY: MMCIF_COORD + CRYSTAL_STRUC_FACTORS;
NMR: MMCIF_COORD + NMR_ACS + NMR_RESTRAINT_*. check_required_files enforces
the per-method rules from onedep_lib's bundled schemas.)

Subcommands:
  prepare --manifest <path>            create/resume local session, register files (no network beyond schema fetch, which is bundled locally)
  preview --manifest <path>            write a human-readable review of exactly what submit will send (read-only, no network)
  dry-run --manifest <path>            check_required_files() only - never calls deposit()
  submit  --manifest <path> --confirm  calls deposit() - real, hard-to-undo submission to wwPDB
  status  --manifest <path>            poll get_status() for an already-submitted deposition
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (  # noqa: E402
    JsonArgumentParser,
    country_enum,
    em_subtype_enum,
    emdb_accession_problem,
    email_problem,
    experiment_type_enum,
    fail,
    file_type_enum,
    iter_schema_issues,
    load_manifest,
    orcid_problem,
    print_json,
    require_fields,
    require_submission_safety_gates,
    run_cli,
    save_manifest,
    save_text,
    short_repr,
)

MAP_LIKE_TYPES = {"EM_MAP", "EM_HALF_MAP", "EM_ADDITIONAL_MAP"}
VOXEL_FIELDS = ("spacing_x", "spacing_y", "spacing_z", "contour")
_MANIFEST_SCHEMA_PATH = Path(__file__).parent / "manifest.schema.json"

# Known MRC/CCP4 data modes, for a friendlier preview summary (not enforced -
# the enforced checks are the 1024-byte header size, the "MAP " format stamp,
# and positive dimensions).
_MRC_MODES = {
    0: "int8", 1: "int16", 2: "float32", 3: "complex int16", 4: "complex float32",
    6: "uint16", 12: "float16", 101: "4-bit",
}


def _build_config():
    """Load config without requiring auth - deposit_init/add_file/check_required_files
    are all local operations and don't need a token. Only submit/status do."""
    from onedep_lib.config import DepositConfig

    return DepositConfig.load()


def _require_auth(config) -> None:
    if config.refresh_token is None:
        fail(
            "Not authenticated. Run "
            "`.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/auth_setup.py check` "
            "for details."
        )


def _require_session(manifest: dict) -> None:
    if not manifest.get("session_id"):
        fail("No session_id in manifest yet. Run `prepare` first.")


def _issues_json(report) -> list[dict]:
    return [{"severity": i.severity.value, "code": i.code, "message": i.message} for i in report.issues]


def _coerce_voxel_floats(voxel: dict, path: str) -> dict[str, float]:
    """Convert each VOXEL_FIELDS value to float, rejecting booleans and
    non-finite results (NaN/Infinity). json.loads() accepts the bare tokens
    NaN/Infinity/-Infinity as a non-standard extension (Python's json module
    does this by default), so a manifest with "spacing_x": NaN parses
    successfully - and bool is an int subclass, so "spacing_x": true would
    otherwise silently become 1.0. Either would sail through as a
    normal-looking float with no complaint until wwPDB's own server-side
    validation sees it at submit time (or worse, a fabricated value gets
    accepted) - catch them locally instead, matching this project's general
    goal of catching what it can before that point."""
    out = {}
    for field in VOXEL_FIELDS:
        raw = voxel[field]
        if isinstance(raw, bool):
            # float(True) is 1.0, so without this check a JSON true/false
            # becomes a plausible-looking spacing/contour of 1.0/0.0.
            fail(f"voxel.{field} for {path!r} is not a valid number: {raw!r}")
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            # OverflowError: a huge JSON integer (e.g. an exponent typo
            # written out in full) is valid JSON but not a valid float.
            fail(f"voxel.{field} for {path!r} is not a valid number: {short_repr(raw)} ({exc})")
        if not math.isfinite(value):
            fail(f"voxel.{field} for {path!r} must be a finite number, got {value}")
        out[field] = value
    return out


def _sniff_mrc(path: str) -> tuple[str | None, str | None]:
    """Lightweight sniff of a map file's fixed 1024-byte MRC2014/CCP4 header
    (no full-file parse, stdlib `struct` only). Returns (summary, problem):
    `summary` is a short human-readable header description (or None if the
    file can't be read), `problem` is a message when the file is clearly not
    a valid MRC/CCP4 map (or None when it looks fine).

    wwPDB requires MRC2014/CCP4-format maps, which carry a `MAP ` stamp at
    byte 208 and positive dimensions. Catching a wrong extension, a
    truncated download, or a mixed-up path here means the depositor hears
    about it at `prepare` time instead of much later at upload or wwPDB's
    own server-side validation. The `MAP ` stamp is shared by MRC2014 and
    CCP4, so requiring it does not reject either accepted format."""
    p = Path(path)
    try:
        with p.open("rb") as fh:
            header = fh.read(1024)
    except OSError as exc:
        return None, f"could not read file: {exc}"

    if len(header) < 1024:
        return (
            None,
            f"file is only {len(header)} bytes - smaller than the 1024-byte MRC/CCP4 "
            "header, so it is not a valid map file (truncated download or wrong file?)",
        )

    nx, ny, nz, mode = struct.unpack("<iiii", header[:16])
    has_map_stamp = header[208:212] == b"MAP "
    mode_desc = _MRC_MODES.get(mode, f"unknown mode {mode}")
    summary = (
        f"{nx}x{ny}x{nz}, {mode_desc}, "
        f"MAP stamp {'present' if has_map_stamp else 'ABSENT'}"
    )

    if not has_map_stamp:
        return (
            summary,
            "no 'MAP ' format stamp at byte 208 - wwPDB requires MRC2014/CCP4 maps, "
            "which always carry this stamp. This file may be truncated, a non-standard "
            "or pre-2014 MRC, or not a map file at all.",
        )
    if nx <= 0 or ny <= 0 or nz <= 0:
        return summary, f"MRC header reports non-positive dimensions ({nx}x{ny}x{nz})"
    return summary, None


def _close_quietly(dep) -> None:
    """Best-effort close, once we already have a real result to report.

    onedep_lib's current close() is a no-op that can't actually raise, but
    nothing guarantees that stays true in a future version, and relying on
    a third-party implementation detail for correctness is the wrong bet -
    a cleanup problem must never turn an already-completed, hard-to-undo
    action (or an already-computed, harmless read like check_required_files)
    into a reported failure. Call this ONLY after the real work for this
    command has already succeeded; a failure that happens before that point
    should still close-and-reraise so it propagates normally."""
    try:
        dep.close()
    except Exception:  # noqa: BLE001 - deliberately swallow, see docstring
        pass


def _open_deposition(manifest: dict, config):
    """Resume the manifest's onedep_lib session, or create one if none exists yet."""
    import onedep_lib as dsp

    session_id = manifest.get("session_id")
    if session_id:
        try:
            return dsp.deposit_resume(session_id, config=config), False
        except KeyError:
            fail(
                f"session_id {session_id!r} from the manifest no longer exists "
                "locally (~/.onedep/sessions). Remove session_id from the "
                "manifest and re-run prepare to start a new session."
            )

    dep = dsp.deposit_init(
        email=manifest["email"],
        users=manifest["users"],
        country=country_enum(manifest["country"]),
        experiment_type=experiment_type_enum(manifest.get("experiment_type", "EM")),
        config=config,
    )
    return dep, True


def _validate_files_section(files: list[dict]) -> None:
    """Value/semantic checks the JSON Schema can't express, run before any
    session is opened so a bad manifest fails clean instead of leaving an
    orphaned local onedep_lib session behind. The schema (see
    _validate_manifest) has already guaranteed each entry is an object with
    string `path`/`file_type`, so this only covers: case-insensitive enum
    resolution, duplicate-path detection, voxel completeness/finiteness for
    map-like types, and an MRC/CCP4 header sniff of map files that exist."""
    seen_paths: dict[str, str] = {}
    for entry in files:
        path = entry["path"]
        ftype_raw = entry["file_type"]
        # Resolving the enum is a pure local lookup, so do it here: an
        # unknown/misspelled file_type must fail before deposit_init runs,
        # not from inside the post-session registration loop.
        ftype = file_type_enum(ftype_raw)
        if path in seen_paths:
            fail(
                f"Manifest lists the same file path twice: {path!r} is used for both "
                f"{seen_paths[path]!r} and {ftype_raw!r}. Each physical file "
                "must be registered under exactly one file_type - onedep_lib doesn't "
                "detect duplicate paths itself, so two entries pointing at the same "
                "file would silently register as two distinct files."
            )
        seen_paths[path] = ftype_raw
        if ftype.name in MAP_LIKE_TYPES:
            # Only map-like files ever have their voxel data read - a "voxel"
            # block on e.g. an ENTRY_IMAGE entry is never used, so don't
            # demand it there; that would only confuse.
            if "voxel" in entry:
                require_fields(entry["voxel"], list(VOXEL_FIELDS), label=f"voxel block for {path!r}")
                # Values too, not just presence - a NaN/boolean/overflowing
                # value must fail before any session exists.
                _coerce_voxel_floats(entry["voxel"], path)
            # Sniff the map file's header if it's on disk (a missing file is
            # left for add_file to report at registration time). Catches a
            # wrong/truncated/non-MRC file locally, before upload.
            if Path(path).is_file():
                _, problem = _sniff_mrc(path)
                if problem:
                    fail(f"{ftype_raw} file {path!r} is not a valid MRC/CCP4 map: {problem}")


def _validate_manifest(manifest: dict) -> None:
    """Every purely-local check, before any session side effect: a manifest
    that fails here has cost nothing - no deposit_init session on disk, no
    add_file registrations, no manifest mutations to clean up.

    Structural validation (required fields, types, array shape) is delegated
    to manifest.schema.json via jsonschema - the same mechanism the EMPIAR
    side uses for JSON_INPUT. Everything the schema can't express (enum
    resolution, duplicate paths, voxel finiteness, MRC header sniff) follows
    in _validate_files_section and the enum calls below."""
    schema = json.loads(_MANIFEST_SCHEMA_PATH.read_text())
    issues = iter_schema_issues(manifest, schema)
    if issues:
        fail("Manifest failed schema validation.", issues=issues)

    # Format checks the schema can't express (schema guarantees these are
    # non-empty strings; here we check they're the RIGHT shape of string).
    problem = email_problem(manifest["email"])
    if problem:
        fail(f"email: {problem}")
    for user in manifest["users"]:
        problem = orcid_problem(user)
        if problem:
            fail(f"users: {problem}")

    # Enum resolution is a pure local lookup - validate before any session
    # exists. _open_deposition re-resolves country/experiment_type when
    # creating; the registration loop re-resolves per-entry file types.
    country_enum(manifest["country"])
    exp_type = experiment_type_enum(manifest.get("experiment_type", "EM"))
    # em_subtype is required (and meaningful) only for EM - onedep_lib's own
    # required-files schema requires a subtype for em and no other method.
    # The JSON Schema can't express "required-if-EM" cleanly across the
    # case-insensitive experiment_type, so enforce it here.
    if exp_type.name == "EM":
        if "em_subtype" not in manifest:
            fail("em_subtype is required for EM experiments (SPA, HELICAL, SUBTOMOGRAM, or TOMOGRAPHY).")
        em_subtype_enum(manifest["em_subtype"])

    for accession in manifest.get("related_emdb", []):
        problem = emdb_accession_problem(accession)
        if problem:
            fail(f"related_emdb: {problem}")

    _validate_files_section(manifest["files"])


def cmd_prepare(manifest_path: str) -> None:
    manifest = load_manifest(manifest_path)
    _validate_manifest(manifest)
    files = manifest["files"]

    config = _build_config()

    dep, created = _open_deposition(manifest, config)
    if created:
        manifest["session_id"] = dep.session_id
        # A fresh session means any file_id values already recorded in the
        # manifest belong to a previous, now-gone session (e.g. a user
        # followed the "session_id no longer exists locally" recovery
        # advice and deleted just session_id). Those ids don't exist in
        # THIS session's store - clear them so the loop below re-registers
        # every file, instead of skipping add_file() and then crashing in
        # set_voxel_values() with a KeyError for an id the new session has
        # never seen.
        for entry in files:
            entry.pop("file_id", None)
        save_manifest(manifest_path, manifest)

    exp_type = experiment_type_enum(manifest.get("experiment_type", "EM"))
    try:
        if exp_type.name == "EM":
            # set_em_params (subtype + voxel later) is EM-only in onedep_lib.
            # Non-EM experiments set only experiment_type (already passed to
            # deposit_init) + coordinates, and are driven by which files are
            # registered; check_required_files enforces the per-method rules.
            dep.set_em_params(
                em_subtype=em_subtype_enum(manifest["em_subtype"]),
                # Validated as a real JSON boolean in _validate_manifest - no
                # bool() coercion, which would read the string "false" as True.
                coordinates=manifest.get("coordinates", False),
            )
        elif "coordinates" in manifest:
            # onedep_lib exposes the coordinates flag only via set_em_params;
            # deposit_init took None, so pass it through here for non-EM too.
            dep.set_em_params(coordinates=manifest["coordinates"])

        for entry in files:
            ftype = file_type_enum(entry["file_type"])
            # The manifest's own file_id is the source of truth for "already
            # registered" - onedep_lib's has_file() is a bool with no way to
            # recover an existing file_id by path, so it can't stand in here.
            if "file_id" not in entry:
                entry["file_id"] = dep.add_file(entry["path"], ftype)
                # Persist after EVERY successful add_file, not once at the
                # end of the loop: if a later file in the list fails (e.g.
                # FileNotFoundError), the files that already succeeded must
                # stay recorded, or a retry will re-add them - registering
                # the same physical file twice in the onedep_lib session
                # store, which nothing else here would catch.
                save_manifest(manifest_path, manifest)
            voxel = entry.get("voxel")
            # Compare against the resolved enum's canonical name, not the raw
            # manifest string - file_type_enum() normalizes case (e.g.
            # "em_map" resolves fine), so a raw-string membership check here
            # would silently skip set_voxel_values() for anything not spelled
            # exactly like the MAP_LIKE_TYPES entries.
            if voxel and ftype.name in MAP_LIKE_TYPES:
                dep.set_voxel_values(entry["file_id"], **_coerce_voxel_floats(voxel, entry["path"]))
    except BaseException:
        _close_quietly(dep)
        raise

    # Everything succeeded and is already durably saved (incrementally,
    # above) - a cleanup problem here must not turn a real success into a
    # reported failure.
    _close_quietly(dep)
    print_json(
        {
            "success": True,
            "session_id": manifest["session_id"],
            "files_registered": len(files),
        }
    )


def cmd_dry_run(manifest_path: str) -> None:
    manifest = load_manifest(manifest_path)
    _require_session(manifest)
    config = _build_config()
    dep, _ = _open_deposition(manifest, config)

    try:
        report = dep.check_required_files()
    except BaseException:
        _close_quietly(dep)
        raise
    _close_quietly(dep)

    print_json({"ok": report.ok, "issues": _issues_json(report)})
    if not report.ok:
        sys.exit(1)


def _file_note(path: str) -> str:
    """A short 'exists / size' note for a manifest file path, for the
    preview. Never raises - a missing file is exactly what preview should
    surface, not error on."""
    p = Path(path)
    try:
        if p.is_file():
            return f"{p.stat().st_size:,} bytes"
        if p.exists():
            return "EXISTS BUT IS NOT A FILE"
        return "MISSING - not found on disk"
    except OSError as exc:
        return f"could not stat: {exc}"


def _render_preview(manifest: dict, manifest_path: str) -> str:
    """Render exactly what `submit` will send, as human-readable Markdown.
    Values are shown post-validation/coercion (voxel numbers as the floats
    that reach onedep_lib, enums as their resolved canonical names), so the
    depositor reviews the real payload, not the raw manifest text. This is a
    faithful rendering of the script's inputs, not a byte-level capture of
    the onedep_lib API request."""
    lines: list[str] = []
    lines.append("# EMDB deposition preview")
    lines.append("")
    lines.append(f"Manifest: `{manifest_path}`")
    lines.append("")
    lines.append(
        "This is a **read-only preview** of what `submit` will send to wwPDB. "
        "Nothing here has been submitted. Review it, then run `dry-run` and "
        "`submit --confirm` when you're ready."
    )
    lines.append("")

    country = country_enum(manifest["country"])
    exp_type = experiment_type_enum(manifest.get("experiment_type", "EM"))
    coordinates = manifest.get("coordinates", False)

    lines.append("## Deposition metadata")
    lines.append("")
    lines.append(f"- **Depositor email:** {manifest['email']}")
    lines.append(f"- **ORCID iD(s):** {', '.join(manifest['users'])}")
    lines.append(f"- **Country:** {country.value} (`{country.name}`)")
    lines.append(f"- **Experiment type:** {exp_type.name}")
    if exp_type.name == "EM":
        lines.append(f"- **EM subtype:** {em_subtype_enum(manifest['em_subtype']).name}")
    lines.append(f"- **Includes fitted coordinates:** {'yes' if coordinates else 'no'}")
    related = manifest.get("related_emdb", [])
    if related:
        lines.append(f"- **Related EMDB entries:** {', '.join(related)}")
    lines.append("")

    lines.append("## Files")
    lines.append("")
    for entry in manifest["files"]:
        ftype = file_type_enum(entry["file_type"])
        lines.append(f"- **{ftype.name}** — `{entry['path']}`  \n  ({_file_note(entry['path'])})")
        voxel = entry.get("voxel")
        if voxel and ftype.name in MAP_LIKE_TYPES:
            coerced = _coerce_voxel_floats(voxel, entry["path"])
            lines.append(
                "  - voxel spacing (Å): "
                f"x={coerced['spacing_x']}, y={coerced['spacing_y']}, z={coerced['spacing_z']}; "
                f"contour level={coerced['contour']}"
            )
        # MRC header summary for map files on disk (validation already ran, so
        # anything shown here passed the sniff - it's a confirmation for the
        # depositor that the file really is the map they meant).
        if ftype.name in MAP_LIKE_TYPES and Path(entry["path"]).is_file():
            summary, _ = _sniff_mrc(entry["path"])
            if summary:
                lines.append(f"  - MRC header: {summary}")
    lines.append("")

    lines.append("## Not covered by this tool")
    lines.append("")
    lines.append(
        "After `submit`, the five detailed experimental sections (Specimen "
        "Preparation, Microscopy, Image Recording, Reconstruction, "
        "Fitting/Interpretation) must still be completed in the OneDep web UI "
        "before the entry can be validated and released. This preview and the "
        "`submit` step cover deposition creation and core file upload only."
    )
    if related:
        lines.append("")
        lines.append(
            "**Related entries:** the deposition API (`onedep_lib`) has no "
            "cross-referencing call, so the related EMDB entries listed above "
            f"({', '.join(related)}) must be linked to this deposition by hand "
            "in the OneDep web UI's \"Related entries\" section after submit."
        )
    lines.append("")
    return "\n".join(lines)


def cmd_preview(manifest_path: str) -> None:
    manifest = load_manifest(manifest_path)
    # Validate exactly as prepare does, so a preview can never show a payload
    # that submit would reject - and so preview is a safe standalone check.
    _validate_manifest(manifest)

    preview = _render_preview(manifest, manifest_path)
    preview_path = Path(manifest_path).with_name("submission_preview.md")
    save_text(preview_path, preview)

    print_json(
        {
            "success": True,
            "preview_path": str(preview_path),
            "preview_markdown": preview,
            "note": (
                "Read-only preview written. Nothing submitted. Show this to the "
                "depositor, then run dry-run and submit --confirm when ready."
            ),
        }
    )


def cmd_submit(manifest_path: str, confirm: bool, force: bool) -> None:
    manifest = load_manifest(manifest_path)
    _require_session(manifest)
    require_submission_safety_gates(confirm, "dry-run", manifest, "remote_dep_id", force, kind="deposition")

    config = _build_config()
    _require_auth(config)
    dep, _ = _open_deposition(manifest, config)

    try:
        report = dep.check_required_files()
    except BaseException:
        _close_quietly(dep)
        raise

    if not report.ok:
        # Close BEFORE printing, not in a shared finally after: closing
        # inside the same try/finally as this print used to risk close()
        # raising while the pending sys.exit(1) below was propagating,
        # which replaces that SystemExit with the close() exception -
        # an Exception subclass, so run_cli's handler would catch it and
        # print a SECOND, unrelated JSON object after this one. Verified
        # this was reproducible before the fix (mocked dep.close() to
        # raise on this exact path).
        _close_quietly(dep)
        print_json(
            {
                "success": False,
                "error": "check_required_files failed - not submitting.",
                "issues": _issues_json(report),
            }
        )
        sys.exit(1)

    try:
        dep_id = dep.deposit()
    except BaseException:
        _close_quietly(dep)
        raise

    # deposit() has already happened for real on wwPDB at this point -
    # nothing from here on may suppress reporting dep_id. Each remaining
    # step (persisting it, reading site_url, closing) is independently
    # best-effort so a failure in one doesn't hide that the submission
    # itself succeeded.
    manifest["remote_dep_id"] = dep_id
    try:
        save_manifest(manifest_path, manifest)
    except Exception:  # noqa: BLE001 - dep_id is still reported below even if this fails
        pass

    site_url = None
    try:
        site_url = dep.site_url
    except Exception:  # noqa: BLE001
        pass

    _close_quietly(dep)

    if site_url:
        manifest["site_url"] = site_url
        try:
            save_manifest(manifest_path, manifest)
        except Exception:  # noqa: BLE001
            pass

    note = (
        "Core files uploaded and processing triggered. The depositor "
        "still needs to complete the detailed experimental sections "
        "(Specimen Preparation, Microscopy, Image Recording, "
        "Reconstruction, Fitting/Interpretation) in the OneDep web UI "
        "at the site_url above before this entry can be validated and released."
    )
    related = manifest.get("related_emdb", [])
    if related:
        note += (
            f" Also link the related entries ({', '.join(related)}) in the web UI's "
            "\"Related entries\" section - the deposition API can't set those."
        )

    print_json(
        {
            "success": True,
            "remote_dep_id": dep_id,
            "site_url": site_url,
            "related_emdb": related or None,
            "note": note,
        }
    )


def cmd_status(manifest_path: str) -> None:
    manifest = load_manifest(manifest_path)
    _require_session(manifest)
    if not manifest.get("remote_dep_id"):
        fail("No remote_dep_id in manifest yet. Run `submit` first.")

    config = _build_config()
    _require_auth(config)
    dep, _ = _open_deposition(manifest, config)
    try:
        status = dep.get_status()
    except BaseException:
        _close_quietly(dep)
        raise
    _close_quietly(dep)

    base = {"remote_dep_id": manifest["remote_dep_id"], "site_url": manifest.get("site_url")}
    if hasattr(status, "status"):
        print_json({**base, "status": status.status})
    else:
        # get_status() can return a DepositError instead of raising - exit
        # non-zero so callers checking the exit code (not just scanning for
        # an "error" key) don't mistake this for a successful status check.
        print_json({**base, "error": str(status)})
        sys.exit(1)


def main() -> None:
    parser = JsonArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    # Each subparser wires its handler via set_defaults(func=...) - unlike a
    # hand-mirrored if/elif dispatch chain, a subcommand can't be registered
    # without a handler, so there's no silent exit-0-with-no-output path.
    p = sub.add_parser("prepare")
    p.add_argument("--manifest", required=True)
    p.set_defaults(func=lambda args: cmd_prepare(args.manifest))

    p = sub.add_parser("preview")
    p.add_argument("--manifest", required=True)
    p.set_defaults(func=lambda args: cmd_preview(args.manifest))

    p = sub.add_parser("dry-run")
    p.add_argument("--manifest", required=True)
    p.set_defaults(func=lambda args: cmd_dry_run(args.manifest))

    p = sub.add_parser("submit")
    p.add_argument("--manifest", required=True)
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=lambda args: cmd_submit(args.manifest, args.confirm, args.force))

    p = sub.add_parser("status")
    p.add_argument("--manifest", required=True)
    p.set_defaults(func=lambda args: cmd_status(args.manifest))

    args = parser.parse_args()
    run_cli(lambda: args.func(args))


if __name__ == "__main__":
    main()
