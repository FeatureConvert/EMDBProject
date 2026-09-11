# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project
is pre-release and not yet versioned.

## [Unreleased] — 2026-09-11 (part 5: read-only EMDB lookups)

Implemented ROADMAP item 3.

### Added

- **`emdb_lookup.py`** — read-only, anonymous lookups against the public
  EMDB API (`emdb` package, re-added to requirements). `lookup --accession
  EMD-XXXX` returns a compact metadata summary (title, authors, sample,
  method, resolution, map format/dimensions/pixel spacing/contour, related
  PDB/EMDB/EMPIAR ids); `exists --accession EMD-XXXX` gives a robust yes/no.
  Network is hit only at call time (not import). It's the only script that
  touches the network. Verified live against EMD-8000 and a nonexistent
  accession.
- The `exists` check handles the installed `emdb` 0.1.12 gotcha where a
  genuine 404 is re-wrapped as `EMDBAPIError` (not `EMDBNotFoundError`): a
  not-found is reported as `exists: false`, but a real network/server error
  is surfaced rather than misreported as "absent".

### Internal / tests

- Network fully mocked in tests (`test_emdb_lookup.py`), including the 404,
  real-error-reraise, nested-author-shape, and sparse-entry paths.
  168 → 176 tests, all passing.

## [Unreleased] — 2026-09-11 (part 4: other experiment types + related entries)

Implemented ROADMAP item 2, and the buildable part of item 1.

### Added

- **Non-EM experiment types** (ROADMAP item 2): the manifest now accepts an
  optional `experiment_type` (default `EM`; also `XRAY`, `NMR`, `SSNMR`,
  `NEUTRON`, `FIBER`, `EC`), resolved case-insensitively. `onedep_lib` is
  experiment-type-agnostic, so a non-EM deposition is driven purely by the
  file types registered, with `check_required_files()` enforcing the
  per-method rules from onedep_lib's bundled schemas. `em_subtype`, the voxel
  block, and the MRC sniff apply to EM only. Verified end-to-end: an X-ray
  deposition (coordinates + structure factors) prepares and dry-runs
  `ok: true` against the real library. EM remains the default and the
  project's focus.
- **`related_emdb`** manifest field (ROADMAP item 1, the buildable part):
  an optional list of `EMD-XXXXX` accessions this entry is composite-related
  to. Format-validated locally; `preview` and `submit` surface them with a
  clear instruction to link them by hand in the OneDep web UI's "Related
  entries" section — because `onedep_lib` exposes **no** deposition-to-
  deposition cross-referencing API (its `related_emdb`/`related_bmrb` fields
  are dormant: no setter, never persisted, never sent). See ROADMAP item 1.

### Internal / tests

- Added `common.experiment_type_enum` and `common.emdb_accession_problem`.
- Coverage for non-EM validation (X-ray accepted without `em_subtype`, EM
  still requires it, unknown type rejected, non-EM skips `set_em_params`),
  and `related_emdb` format + preview surfacing. 146 → 168 tests, all passing.

## [Unreleased] — 2026-09-11 (part 3: ORCID/email validation)

Finished the remaining half of ROADMAP item 5.

### Added

- **ORCID iD validation** (`common.orcid_problem`): the manifest's `users`
  entries are checked for the `0000-0002-1825-0097` structure and the ISO
  7064 MOD 11-2 checksum (final character may be `X`), tolerating an
  `https://orcid.org/` prefix. A transposed or truncated iD now fails at
  `prepare`/`preview` instead of reaching wwPDB.
- **Email sanity check** (`common.email_problem`): the manifest's `email` is
  checked for an obvious shape (one `@`, a dotted domain, no spaces) —
  deliberately permissive, just catching fat-finger mistakes.

### Changed

- `.gitignore` now ignores `*.pdf` (README.pdf duplicated README.md and the
  PDFs are large binaries).

### Internal / tests

- Added ORCID (valid, bad-checksum, malformed, trailing-`X`) and email
  (valid/invalid) coverage. 128 → 146 tests, all passing.

## [Unreleased] — 2026-09-11 (part 2: roadmap items 4 & 5)

Implemented two researched roadmap items: a local MRC map-file header check
and a JSON Schema for the manifest.

### Added

- **Local MRC2014/CCP4 header sniff** (ROADMAP item 4). `em_deposit.py`
  reads each map-like file's fixed 1024-byte header (stdlib `struct`, no
  full-file parse) during `prepare`/`preview`, before any session is opened,
  and rejects a file that is smaller than the header, lacks the `MAP ` stamp
  at byte 208, or reports non-positive dimensions — catching a truncated
  download, wrong extension, or mixed-up path locally. `preview` shows the
  sniffed header (dimensions, data mode, stamp) per map file.
- **`manifest.schema.json`** (ROADMAP item 5): a Draft-7 JSON Schema for the
  EMDB manifest, enforced with `jsonschema` (already a dependency), doubling
  as precise documentation of the format.

### Changed

- `em_deposit.py` manifest validation now delegates structural checks
  (required fields, types, array shape, `coordinates` boolean) to the JSON
  Schema, via a shared `common.iter_schema_issues()` helper that the EMPIAR
  JSON_INPUT validator now also uses. The hand-rolled `require_fields` /
  isinstance / `require_type` manifest checks are retired; enum resolution,
  voxel finiteness, and the MRC sniff remain as semantic checks the schema
  can't express. Schema failures are reported as the standard
  `{"success": false, "error": ..., "issues": [...]}` shape.

### Internal / tests

- Added tests for the MRC sniff (too-small, missing stamp, non-positive
  dims, and the preview header summary) and for schema-driven manifest
  errors; the `fails_json` fixture now matches substrings against the
  `issues` list as well as the error message. 124 → 128 tests, all passing.

## [Unreleased] — 2026-09-11 (part 1: review fixes + preview)

A max-effort multi-angle code review of the previous change set (10 finder
angles + verification + gap sweep), followed by fixing every confirmed
finding, plus a new pre-submission **preview** feature.

### Added

- **`em_deposit.py preview --manifest <path>`** — writes a read-only,
  human-readable `submission_preview.md` next to the manifest (and returns the
  same Markdown in `preview_markdown`) rendering exactly what `submit` will
  send: country and EM subtype resolved to canonical forms, voxel numbers as
  the coerced floats, and each file's on-disk size or a `MISSING` flag. No
  network, no session created. Intended as the review artifact to show the
  depositor before submitting.
- **`empiar_deposit.py preview --json-input <path> [--data-dir <path>]`** —
  writes a read-only `json_input.preview.md`: schema-validation result, title,
  each imageset and its referenced directory (resolved under `--data-dir` when
  given), and the full pretty-printed JSON_INPUT payload (which *is* the EMPIAR
  submission format).
- `common.save_text()` — atomic plain-text writer (same crash-safety as
  `save_manifest`), used by the preview commands.
- `list_depositions.py` now reports a submitted EMPIAR entry even when its
  JSON_INPUT file was later deleted or renamed (via its orphaned submission
  marker).

### Fixed

- **Voxel values could be silently wrong.** `_coerce_voxel_floats` accepted
  JSON booleans (`float(True) == 1.0`) and let a huge JSON integer escape as an
  uncaught `OverflowError`. Booleans are now rejected, `OverflowError` is caught
  with the same clean per-field message, and the check runs *before* any remote
  session is created.
- **`coordinates` field was coerced with `bool()`**, so the string `"false"`
  became `True` and silently registered a non-existent atomic model. It is now
  validated as a real JSON boolean.
- **`email`/`users` were never validated for type** — a single ORCID written as
  a bare string (instead of a list) would have reached wwPDB at submit time.
  Both are now validated locally, pre-session.
- **Local validation ran after side effects.** Voxel-value checks and
  `file_type`/`em_subtype`/`country` enum resolution now happen in a single
  pre-session validation pass, so a bad manifest fails before any local
  onedep_lib session is created (no orphaned sessions left behind).
- **`list_depositions.py` aborted the whole listing on one bad file.** A single
  corrupt/unreadable manifest or marker (invalid JSON, non-UTF-8 bytes, or a
  directory in place of a file) is now reported as that one slug's own error;
  healthy depositions still show. It also no longer treats non-object JSON as a
  crash, and no longer lists hidden dotfiles (e.g. macOS `._*.json` AppleDouble
  siblings) as phantom JSON_INPUTs.
- **Non-object JSON crashed loaders generically.** `load_manifest` /
  `load_optional_json` now fail cleanly naming the file if a JSON file doesn't
  contain an object, instead of a downstream `AttributeError`.
- **A JSON_INPUT named `*.submitted.json`** would collide with the submission
  marker scheme; `empiar_deposit.py` now rejects such a path up front.
- **`auth_setup.py check`** now derives its exit code from a single
  end-of-function decision (was per-branch `sys.exit` kept in sync by comment).
- Documentation corrections: `onedep_lib`'s license is Apache-2.0 (was
  mis-stated as MIT); the submission-marker sidecar filename notation
  (`.json` → `.submitted.json`, replaced not appended); a self-contradiction in
  `em_deposition_fields.md` about what is validated locally; and the error-
  contract / script-count references (now four scripts) in the README and
  troubleshooting reference.

### Internal / tests

- Root-caused a vacuous test: `conftest.load_script_module` now registers
  modules in `sys.modules`, so `patch("module.symbol")` reaches the module
  under test (previously it patched a second, freshly-imported copy and the
  patch silently no-op'd). The affected test also now asserts the mock was
  actually called so it can't regress to vacuous.
- Shared `require_type`/`require_str`/`short_repr` and `submitted_marker_path`
  helpers in `common.py` replace hand-rolled, drift-prone copies; a `fails_json`
  fixture replaces repeated exit-code/JSON assertion scaffolding.
- CLI dispatch in every script now wires handlers via `set_defaults(func=...)`
  instead of a hand-mirrored `if/elif` chain (a registered subcommand can no
  longer silently exit 0 with no output).
- Test suite grew from 90 to 124 tests, all passing.
