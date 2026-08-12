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
.venv/bin/pip install -r .claude/skills/emdb-deposition/requirements.txt
```

**`ModuleNotFoundError: No module named 'onedep_lib'`**
You ran a script with the system `python3` instead of `.venv/bin/python3`.
Every command in this project should be prefixed with `.venv/bin/python3`,
not a bare `python3`.

## Authentication (OneDep / EMDB)

**`Not authenticated. Run 'python3 auth_setup.py check' for details.`**
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

**`<path> is not valid JSON: ...`**
Syntax error in the manifest — usually a trailing comma or an unescaped
quote. The error includes the exact JSON parse failure location.

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
A file listed in the manifest doesn't exist at that path. Check for typos
or a file that moved after the manifest was written.

**`session_id '<id>' from the manifest no longer exists locally (~/.onedep/sessions). Remove session_id from the manifest and re-run prepare to start a new session.`**
Local session state (under `~/.onedep/sessions`) was cleared, or you're
running on a different machine than the one that ran `prepare`. If this
deposition was never actually `submit`ted (no `remote_dep_id` in the
manifest yet), it's safe to delete the `session_id` field and re-run
`prepare` — it'll register all the files fresh. If it *was* already
submitted, the remote deposition still exists on wwPDB (check its
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

**`This manifest already has remote_dep_id='D_...'. Re-running submit will re-upload files against the existing deposition. Pass --force if that's intentional.`**
You (or the skill) already ran `submit` successfully once for this
manifest. Re-running without `--force` is blocked because it would
re-upload every file to the existing deposition — usually not what you
want unless you're deliberately adding more files to it.

## Real API errors from `deposit()` / `status()`

These come directly from `onedep_lib`'s HTTP layer, not from our wrapper —
read the message text itself, it's usually specific:

- **`ApiUnreachableError`** — the request never reached the server at all
  (DNS failure, connection refused, timeout, TLS error). Check your
  network connection and try again.
- **`ApiError` with a status code** — the server responded but rejected
  the request. A 401/403 usually means your access token expired mid-way
  (rare, since it auto-refreshes) — re-run `auth_setup.py check`. Other
  codes come with wwPDB's own error message in the exception text.

## `empiar_deposit.py`

**`EMPIAR_API_TOKEN is not set. Get one from https://www.ebi.ac.uk/empiar/deposition/api_token/ and export it in your own shell before running submit.`**
Exactly what it says — see
[setup_checklist.md](setup_checklist.md#3-empiar-access-only-needed-if-youre-also-depositing-raw-image-data).

**`EMPIAR_TRANSFER_PASS is not set (this is the transfer password EMPIAR issued you, not your account password or API token).`**
This is a *third*, separate credential from your EMPIAR password and API
token — EMPIAR issues it specifically for data transfer. Check your email
from the EMPIAR team, or ask EMPIAR support if you can't find it.

**`Data directory not found: <path>`**
The `--data-dir` you passed doesn't exist. This should contain
subdirectories matching each imageset's `directory` field in the
JSON_INPUT.

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
.venv/bin/python3 -m pytest .claude/skills/emdb-deposition/scripts/tests/ -v
```
If tests pass but you're still stuck, the issue is almost certainly on the
wwPDB/EMPIAR server side or in the deposition data itself — contact
`emdbhelp@ebi.ac.uk` (EMDB) or EMPIAR support, quoting the exact error
message and, if you have one, the `remote_dep_id`.
