# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project
is pre-release and not yet versioned.

## [Unreleased] — 2026-09-11

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
