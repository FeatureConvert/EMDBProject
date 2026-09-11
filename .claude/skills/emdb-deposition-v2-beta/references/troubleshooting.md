# Troubleshooting

Every error message below is quoted verbatim from either the wrapper
scripts or the underlying libraries (`onedep_lib`, `empiar_depositor`) —
if what you're seeing doesn't match anything here, search for the message
text directly in `scripts/*.py` (our own errors) or in
`.venv/lib/python*/site-packages/onedep_lib/` and
`.venv/lib/python*/site-packages/empiar_depositor/` (library errors) to
find exactly where it comes from.

## Setup / installation

**`error: externally-managed-environment` from pip**
You ran `pip install` outside the project's virtual environment — macOS's
Homebrew Python refuses global installs. Use the venv instead:
```bash
python3 -m venv .venv
.venv/bin/pip install -r .claude/skills/emdb-deposition-v2-beta/requirements.txt
```

**`ModuleNotFoundError: No module named 'onedep_lib'`**
You ran a script with the system `python3` instead of `.venv/bin/python3`.
Every command in this project should be prefixed with `.venv/bin/python3`,
not a bare `python3`.

## Authentication (OneDep / EMDB)

**`Not authenticated. Run '.venv/bin/python3 .claude/skills/emdb-deposition-v2-beta/scripts/auth_setup.py check' for details.`**
This is our own message from `em_deposit.py submit`/`status` — you haven't
logged in yet, or your token expired. Follow
[setup_checklist.md](setup_checklist.md) to get a fresh refresh token, then
run `auth_setup.py login`. Note `prepare` and `dry-run` don't need this —
only `submit` and `status` actually talk to the server.

**`AuthError: No refresh token stored. Paste a refresh token first.`**
Raw error from `onedep_lib` itself — same root cause as above, just
surfacing from a different call path. `auth_setup.py check` should catch
this before you get here; if you're seeing it from somewhere else, run
`check` first to confirm.

**`AuthError: Refresh token is expired, revoked, or invalid; generate and paste a new token pair.`**
Refresh tokens expire after 30 days (access tokens after 30 minutes, but
those refresh automatically — this is about the refresh token itself).
Get a new one from the "Deposition API" section at
https://deposit-pdbe.wwpdb.org/deposition/ and run `auth_setup.py login`
again.

**Login "succeeds" but then immediately fails again, or keeps reusing an old token**
You left `ONEDEP_REFRESH_TOKEN` set in your shell after a previous login.
`onedep_lib` treats an env-var token as higher priority than the one it
just persisted to `~/.config/onedep/config.toml`, and refresh tokens
rotate on every use — so a stale env var overwrites the good, rotated
token with the dead original on every run. Fix: `unset ONEDEP_REFRESH_TOKEN`
and try again. Check with `echo $ONEDEP_REFRESH_TOKEN` if unsure whether
it's still set.

## `em_deposit.py` — manifest and file errors

**`Manifest not found: <path>`**
Path typo, or you're running the command from the wrong working directory.
Manifests live under `depositions/<slug>/manifest.json` at the project
root — use an absolute path if you're unsure of your current directory.

**`Manifest at <path> is not valid JSON: ...`**
Syntax error in the manifest — usually a trailing comma or an unescaped
quote. The error includes the exact JSON parse failure location. (For
`empiar_deposit.py`, the equivalent message says `JSON_INPUT at <path> is
not valid JSON: ...` — both share the same underlying loader.)

**`Manifest is missing required field(s): <list>`** (or, for a specific `files[]` entry: **`Each files[] entry is missing required field(s): <list>`**, or for a voxel block: **`voxel block for '<path>' is missing required field(s): <list>`**)
`require_fields()`'s message includes a label saying exactly what's
missing the field(s) — the manifest itself (top-level `email`, `users`,
`country`, `em_subtype`, `files`), a specific `files[]` entry (`path`/
`file_type`), or a specific file's `voxel` block (`spacing_x`/`spacing_y`/
`spacing_z`/`contour`). Add the missing field(s) and re-run `prepare`.

**`Manifest lists the same file path twice: '<path>' is used for both '<type1>' and '<type2>'. ...`**
Two entries in `files[]` point at the same physical file. `onedep_lib`
doesn't detect duplicate paths on its own — two entries pointing at the
same file would otherwise silently register as two distinct files and
could pass the required-file *count* check (e.g. looking like two valid
half-maps) while actually uploading the same bytes twice under different
roles. Fix the manifest so each physical file appears in exactly one
`files[]` entry.

**`Unknown country: '<value>'. Use a Country enum name ... or its exact wwPDB display value ...`**
Use either the short enum form (`UK`, `USA`) or the exact display string
(`"United Kingdom"`, `"United States"`) — see
[em_deposition_fields.md](em_deposition_fields.md) for how matching works.

**`Unknown EM subtype: '<value>'. Expected one of: SPA, HELICAL, SUBTOMOGRAM, TOMOGRAPHY.`**
Self-explanatory — check the manifest's `em_subtype` field against that
list (case-insensitive, but must be one of those four words).

**`Unknown file type: '<value>'. Expected one of: ...`**
The manifest's `file_type` must match a name from `onedep_lib.FileType`
exactly (e.g. `EM_MAP`, `EM_HALF_MAP`, `ENTRY_IMAGE`, `MMCIF_COORD`) — see
the table in [em_deposition_fields.md](em_deposition_fields.md).

**`FileNotFoundError: File not found: <path>`**
Raised by `onedep_lib` itself (not this project's own scripts — you won't
find this exact string by grepping `scripts/*.py`; look in
`.venv/lib/python*/site-packages/onedep_lib/dsp.py` instead) when a file
listed in the manifest doesn't exist at that path. Every script's `main()`
catches this and any other uncaught exception centrally (see
[Every script's error contract](#every-scripts-error-contract) below), so
you'll see it as a normal `{"success": false, "error": "FileNotFoundError:
File not found: ..."}` JSON line rather than a raw traceback. Check for
typos or a file that moved after the manifest was written.

**`voxel block for '<path>' must be an object with fields [...], got NoneType: None`** (or similar; the label varies by which field is wrong)
A manifest field that should be an object — most commonly a file's `voxel`
block — was written as `null`, a string, or some other non-object value,
often from an unfilled template field. Fix the manifest so that field is a
proper JSON object with the expected sub-fields.

**`session_id '<id>' from the manifest no longer exists locally (~/.onedep/sessions). Remove session_id from the manifest and re-run prepare to start a new session.`**
Local session state (under `~/.onedep/sessions`) was cleared, or you're
running on a different machine than the one that ran `prepare`. If this
deposition was never actually `submit`ted (no `remote_dep_id` in the
manifest yet), it's safe to delete the `session_id` field and re-run
`prepare` — it'll register all the files fresh (`prepare` automatically
clears any `file_id` values left over from the old session when it
creates a new one, so you don't need to remove those by hand). If it *was*
already submitted, the remote deposition still exists on wwPDB (check its
`site_url`); you just can't resume the local staging session for it.

**`dry-run` reports `"ok": false` with issues like these — what they mean:**

| Message | Fix |
|---|---|
| `an image file is required for em` | Add an `ENTRY_IMAGE` file entry. |
| `only one image file is allowed for em` | Remove extra `ENTRY_IMAGE` entries — exactly one. |
| `a map file is required for em` | Add an `EM_MAP` file entry. |
| `only one map file is allowed for em` | Remove extra `EM_MAP` entries — exactly one. |
| `half-map is required for single, helical, and subtomogram` / `exactly two half-map files are required for single, helical, and subtomogram` | You need exactly two `EM_HALF_MAP` entries (not required for `TOMOGRAPHY`). |
| `only one reflections file is allowed for em` | Only relevant if you added `xs-cif`/`xs-mtz` files — remove extras. |

These come straight from the bundled `required_files.json` schema and only
check file *types and counts* — not file contents. A `"ok": true` dry-run
does not mean the map files themselves are valid; corrupt or malformed
files will only surface at `submit`/server-processing time.

## `em_deposit.py submit` gating

**`Refusing to submit without --confirm. Run 'dry-run' first and review it with the user.`**
Working as intended — this is the safety gate. Run `dry-run`, review the
report, get explicit confirmation, then add `--confirm`.

**`This deposition already has remote_dep_id='D_...'. Re-running submit will re-run against the existing deposition. Pass --force if that's intentional.`**
You (or the skill) already ran `submit` successfully once for this
manifest. Re-running without `--force` is blocked because it would
re-run against the existing deposition — usually not what you want unless
you're deliberately adding more files to it.

## Real API errors from `deposit()` / `status()` / `check_auth_key()`

These come directly from `onedep_lib`'s HTTP layer, not from our wrapper —
read the message text itself, it's usually specific:

- **`ApiUnreachableError`** — the request never reached the server at all
  (DNS failure, connection refused, timeout, TLS error). Check your
  network connection and try again.
- **`ApiError` with a status code** — the server responded but rejected
  the request. A 401/403 usually means your access token expired mid-way
  (rare, since it auto-refreshes) — re-run `auth_setup.py check`. Other
  codes come with wwPDB's own error message in the exception text.
- **`ConfigError: Failed to parse .../config.toml: ...`** — the local
  `~/.config/onedep/config.toml` is malformed (hand-edited and broken, or
  corrupted). Can surface from any of the three scripts, since all of them
  call `DepositConfig.load()`. Fix or delete the file and re-run
  `auth_setup.py login`.

## Every script's error contract

`auth_setup.py`, `em_deposit.py`, `empiar_deposit.py`,
`list_depositions.py`, and `emdb_lookup.py` each wrap their entire `main()`
dispatch in `common.run_cli()`, which catches literally any exception (not an
enumerated list) and converts it to
`{"success": false, "error": "<ExceptionType>: <message>"}` on stdout with
exit code 1. CLI *usage* errors (a missing/invalid argument, an unknown
subcommand) are covered too: every script uses `common.JsonArgumentParser`,
whose `error()` routes argparse failures through the same JSON contract
(exit code 1) instead of argparse's default plain-text-to-stderr + exit 2.
The only things `run_cli()` doesn't catch are `SystemExit` (raised by
`fail()`, and by argparse for `--help`, which correctly still prints its
plain-text help and exits 0) and `KeyboardInterrupt`. If you see a raw
Python traceback instead of JSON from any of these five scripts, that's
itself a bug worth reporting — it should be structurally impossible.

(`emdb_lookup.py` is the one script that makes network calls — read-only,
anonymous, to the public EMDB API. A network failure or a genuine 404 is
still reported through the same JSON contract, not as a traceback.)

## `empiar_deposit.py`

**`EMPIAR_API_TOKEN is not set. Get one from https://www.ebi.ac.uk/empiar/deposition/api_token/ and export it in your own shell before running submit.`**
Exactly what it says — see
[setup_checklist.md](setup_checklist.md#3-empiar-access-only-needed-if-youre-also-depositing-raw-image-data).

**`EMPIAR_TRANSFER_PASS is not set (this is the transfer password EMPIAR issued you, not your account password or API token). See references/setup_checklist.md.`**
This is a *third*, separate credential from your EMPIAR password and API
token — EMPIAR issues it specifically for data transfer. Check your email
from the EMPIAR team, or ask EMPIAR support if you can't find it.

**`Data directory not found: <path>`**
The `--data-dir` you passed doesn't exist. This should contain
subdirectories matching each imageset's `directory` field in the
JSON_INPUT.

**`Neither --ascp resolved (no Aspera Connect found at the default install location) nor --globus was given. ...`**
This is a wrapper-side convenience check, not a workaround for a real gap
in `empiar-depositor` itself — its own CLI already refuses to run at all
(confirmed by reading its installed source) if neither is available,
before creating anything. This check just fails faster, with a clearer
message, and without spawning a subprocess. Install Aspera Connect at its
default OS location (see
[setup_checklist.md](setup_checklist.md#4-install-a-transfer-tool-for-empiar))
so it's auto-detected, or pass `--ascp /path/to/ascp` / `--globus <uuid>`
explicitly.

**`This EMPIAR entry already has entry_id='<id>'. ... Pass --force if that's intentional.`**
A prior `submit` against this exact JSON_INPUT path already succeeded — the
sidecar `<json_input>.submitted.json` file records the entry_id from that
run. Re-running without `--force` is blocked because empiar-depositor would
attempt to create a *second* live entry and re-transfer the data. Pass
`--force` only if that's genuinely intentional (e.g. depositing a
deliberately separate entry from an edited copy of the same JSON_INPUT). If
you're instead resuming an interrupted transfer against that *same* entry,
use `--resume <entry_id> <entry_dir>` — that bypasses this guard entirely
without needing `--force` (which would mean the opposite: a deliberately
separate entry).

**`submit` output includes a `warning` about the entry ID not being parseable, with a placeholder `entry_id` starting `unparsed-success-`**
`submit` parses `entry_id`/`entry_directory` out of empiar-depositor's own
stdout via a fixed pattern. If `returncode` was 0 but that pattern didn't
match (e.g. a future empiar-depositor version changes its wording), a
placeholder marker is written anyway so a resubmission is still blocked
without `--force` — but you should check `stdout_tail` and your EMPIAR
account directly to find the real entry, since this script doesn't have
it. This is a should-never-happen safety net, not routine behavior; if you
see it, the regex in `empiar_deposit.py` (`_ENTRY_ID_RE`) likely needs
updating to match the new wording.

**`JSON_INPUT failed schema validation - not submitting.` (with an `issues` list)**
`submit` re-runs the same schema validation `validate` does, right before
shelling out, in case the file was edited since the last `validate` call.
Fix the listed issues and re-run `submit` (or `validate` first if you want
to iterate without touching credentials/transfer).

**`empiar-depositor executable not found at '<path>'. Re-run '.venv/bin/pip install -r .claude/skills/emdb-deposition-v2-beta/requirements.txt' to reinstall it.`**
The console script isn't where this script expects it (next to the current
Python interpreter) — usually means the venv's dependencies weren't fully
installed, or something removed the entry point after install. Reinstall
as the message says.

**`validate` reports `"ok": false` with a list of `{"path": ..., "message": ...}` issues**
These are raw `jsonschema` validation errors against
`empiar_deposition.schema.json` (bundled in the installed
`empiar_depositor` package). `path` tells you which field is wrong
(e.g. `imagesets.0.category`); look up that field's meaning and valid
values either in the schema file directly or at
https://www.ebi.ac.uk/empiar/deposition/json_submission.

**`submit` fails with a non-zero exit and prints `stdout_tail`/`stderr_tail`**
That's the real output from the `empiar-depositor` CLI (only the last
~4000 characters of each, to avoid flooding the conversation) — read it
directly, it's usually specific about what failed. Common causes:
  - **`ascp: command not found`** or similar — Aspera Connect isn't
    installed, or isn't at the default location. Install it, or pass
    `--ascp /path/to/ascp` to `empiar_deposit.py submit` to point at it
    directly.
  - **Globus errors** — you may need to run `globus login` once first
    (see [setup_checklist.md](setup_checklist.md)), or pass `--globus`
    with a force-login if credentials are stale.
  - **Transfer interrupted partway** — use `--resume <entry_id> <entry_dir>`
    to continue an Aspera upload rather than starting over.

## Nothing here matches

Run the test suite to confirm the scripts themselves are working correctly
before assuming a bug in this project:
```bash
.venv/bin/python3 -m pytest .claude/skills/emdb-deposition-v2-beta/scripts/tests/ -v
```
If tests pass but you're still stuck, the issue is almost certainly on the
wwPDB/EMPIAR server side or in the deposition data itself — contact
`emdbhelp@ebi.ac.uk` (EMDB) or EMPIAR support, quoting the exact error
message and, if you have one, the `remote_dep_id`.
