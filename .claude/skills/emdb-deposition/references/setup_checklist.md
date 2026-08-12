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
3. In your own shell (not through Claude), run:

   ```bash
   ONEDEP_REFRESH_TOKEN="<paste-the-token>" python3 scripts/auth_setup.py login
   ```

   This validates the token and writes it to
   `~/.config/onedep/config.toml`, outside the project directory.
4. **Immediately after**, run `unset ONEDEP_REFRESH_TOKEN` in that shell.
   The token rotates on every use; onedep_lib prefers an env-var token over
   the one it just persisted to disk, so leaving the env var set will keep
   overriding the fresh, rotated token with the stale original and break
   future logins.
5. Never paste the token into chat for Claude to type or store — run the
   `login` command yourself in your own terminal.

After 30 days (or if `auth_setup.py check` reports unauthenticated),
repeat steps 1–4 to get a new token.

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

EMPIAR uploads move over Aspera or Globus — pick one:

- **Aspera** (default): install IBM Aspera Connect, which provides the
  `ascp` binary. https://www.ibm.com/aspera/connect/
- **Globus**: `pip install globus-cli==1.7.0` (or the version
  `empiar-depositor` currently pins) and complete `globus login` once.

Only needed once you actually reach an EMPIAR deposition — not required to
use the EMDB map deposition path.

## 5. Verify

Once ORCID login succeeds, `~/.config/onedep/config.toml` should exist with
an `[auths.<fqdn>]` section (token values, not shown by the skill). Run
`python3 scripts/auth_setup.py check` to confirm without exposing secrets.

## Open items

- No wwPDB sandbox/staging deposition endpoint was found in `onedep_lib`'s
  source — the default and only documented hostname is
  `https://deposit.wwpdb.org/deposition`. Treat every `deposit()` call as
  hitting production; there is no safe environment to test a real
  submission against.
