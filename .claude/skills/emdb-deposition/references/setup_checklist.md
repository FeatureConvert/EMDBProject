# One-time setup checklist

Do these once before the first deposition. Everything here happens outside
this project — accounts, tokens, and installed tools — nothing in this
checklist gets committed to the project directory.

## 1. ORCID account

You need an ORCID iD to authenticate with wwPDB OneDep.

- Create one at https://orcid.org/register if you don't have one.
- Note your ORCID iD (format `0000-0002-XXXX-XXXX`) — the skill will ask for it.

## 2. wwPDB / OneDep API refresh token

`onedep_lib` (the library this skill drives) does **not** perform the ORCID
login itself — it only stores and rotates a token you get from wwPDB's own
web portal. Confirmed by reading the installed package source:

1. Go to https://deposit-pdbe.wwpdb.org/deposition/ in your own browser and
   click "Sign in with ORCID".
2. Scroll to the **"Deposition API"** section of the page and generate an
   API refresh token. It's valid 30 days and **shown only once** — copy it
   immediately.
3. **Important — hostname mismatch:** `onedep_lib` defaults to talking to
   `https://deposit.wwpdb.org/deposition`, but the token you just generated
   is scoped to `https://deposit-pdbe.wwpdb.org/deposition` (the PDBe site
   from step 1) — a *different* host. Refresh tokens are host-scoped, so if
   you skip setting `ONEDEP_HOSTNAME` to match, login fails with `"Refresh
   token is expired, revoked, or invalid"` even for a token you just
   generated seconds ago. This isn't really an expired-token error — it's a
   site mismatch that happens to produce the same message. You must set
   `ONEDEP_HOSTNAME` in the same shell, every time, before `login` (and
   before any later `check`/`status`/`submit`, since the hostname itself is
   **not** persisted to `config.toml` the way the token pair is):

   ```bash
   export ONEDEP_HOSTNAME=https://deposit-pdbe.wwpdb.org/deposition
   ```

   Add that line to your shell profile (e.g. `~/.zshrc`) so it's always set
   — otherwise every new terminal session will silently fall back to the
   wrong host and every command will fail with the same misleading error.
4. In your own shell (not through Claude), with `ONEDEP_HOSTNAME` set as
   above, run:

   ```bash
   ONEDEP_REFRESH_TOKEN="<paste-the-token>" .venv/bin/python3 .claude/skills/emdb-deposition/scripts/auth_setup.py login
   ```

   (run from the project root; use the project's venv, not a bare `python3`
   — a bare `python3` won't have `onedep_lib` installed.)

   This validates the token and writes it to
   `~/.config/onedep/config.toml`, outside the project directory.
5. **Immediately after**, run `unset ONEDEP_REFRESH_TOKEN` in that shell.
   The token rotates on every use; onedep_lib prefers an env-var token over
   the one it just persisted to disk, so leaving the env var set will keep
   overriding the fresh, rotated token with the stale original and break
   future logins.
6. Never paste the token into chat for Claude to type or store — run the
   `login` command yourself in your own terminal.

After 30 days (or if `auth_setup.py check` reports unauthenticated),
repeat steps 1–5 to get a new token.

## 3. EMPIAR access (only needed if you're also depositing raw image data)

- Get an EMPIAR API token from https://www.ebi.ac.uk/empiar/deposition/api_token/
  (log in first, then generate a token).
- EMPIAR will also issue you a separate **transfer password**
  (`EMPIAR_TRANSFER_PASS`) — this is not your account password. Set it as an
  environment variable in your own shell profile, e.g. in `~/.zshrc`:

  ```bash
  export EMPIAR_TRANSFER_PASS="the-password-empiar-gave-you"
  ```

  Never paste this into chat for Claude to write into a file — the skill
  reads it from your environment only.

- Set your EMPIAR API token the same way, as `EMPIAR_API_TOKEN`:

  ```bash
  export EMPIAR_API_TOKEN="your-empiar-api-token"
  ```

  Never pasted into chat or passed as a CLI argument — `empiar_deposit.py`
  reads both env vars directly and never prints them.

## 4. Install a transfer tool for EMPIAR

EMPIAR uploads move over Aspera or Globus — this is **not optional** once
you reach an EMPIAR submission: `empiar_deposit.py submit` refuses to run
at all unless one of them resolves. (`empiar-depositor`'s own CLI would
also refuse without one — this project's wrapper just checks earlier, with
a clearer message, and without spawning a subprocess.) Pick one:

- **Aspera**: install IBM Aspera Connect, which provides the `ascp`
  binary, at its default location for your OS
  (`~/Applications/Aspera Connect.app/...` on macOS, `~/.aspera/connect/bin`
  on Linux) — `empiar_deposit.py` auto-detects it there, so you don't need
  to pass `--ascp` yourself once it's installed.
  https://www.ibm.com/aspera/connect/
- **Globus**: `pip install globus-cli==1.7.0` (or the version
  `empiar-depositor` currently pins) and complete `globus login` once, then
  pass `--globus <your-uuid>` explicitly on `submit`.

Only needed once you actually reach an EMPIAR deposition — not required to
use the EMDB map deposition path.

## 5. Verify

Once ORCID login succeeds, `~/.config/onedep/config.toml` should exist with
an `[auths.<fqdn>]` section (token values, not shown by the skill). With
`ONEDEP_HOSTNAME` still set to `https://deposit-pdbe.wwpdb.org/deposition`
(see step 2.3 above — it's not persisted, so it must be set in this shell
too), run
`.venv/bin/python3 .claude/skills/emdb-deposition/scripts/auth_setup.py check`
to confirm without exposing secrets.

## Open items

- No wwPDB sandbox/staging deposition endpoint was found in `onedep_lib`'s
  source — the library's built-in default hostname is
  `https://deposit.wwpdb.org/deposition`, but tokens generated from the
  PDBe portal (step 2 above) are scoped to
  `https://deposit-pdbe.wwpdb.org/deposition`, so `ONEDEP_HOSTNAME` must
  point there instead. Either way, treat every `deposit()` call as hitting
  production; there is no safe environment to test a real submission
  against.
