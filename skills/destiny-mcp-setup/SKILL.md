---
name: destiny-mcp-setup
description: Install, authenticate, register, verify, and troubleshoot the local Destiny MCP server in Codex. Use when setting up this repository for a new user, configuring Bungie API or OAuth credentials, registering the stdio server with codex mcp, handling the HTTPS localhost callback, diagnosing ERR_CONNECTION_REFUSED or missing secret and token failures, or confirming the normal eight-tool profile.
---

# Destiny MCP Setup

Complete the setup through a real MCP handshake. Do not stop after creating the virtual environment or adding a Codex configuration entry.

## Protect credentials and existing work

- Inspect the worktree before editing. Preserve unrelated user changes.
- Never print `.env`, API keys, client secrets, access tokens, refresh tokens, or callback URLs containing an authorization code.
- Ask the user to enter secrets directly in `.env`; do not ask them to paste secrets into chat.
- Ensure `.env` is ignored by Git and set its permissions to `0600` on POSIX systems.
- If a credential was pasted into chat, recommend rotating it in the Bungie Developer Portal after setup and updating `.env`.
- Do not overwrite an existing `.env`, OAuth token, or Codex MCP entry without inspecting it first.

## 1. Inspect the checkout

Resolve and retain the absolute repository path. Paths can contain spaces or non-ASCII characters, so quote them in shell commands and use absolute paths in the Codex MCP entry.

Check the expected files and current state:

```bash
python3 --version
git status --short
ls -la .env.example .venv/bin/python .venv/bin/destiny-mcp
codex mcp get destiny
```

Treat a missing `.venv` or missing `destiny-mcp` executable as an installation prerequisite. Treat `codex mcp get destiny` returning “not found” as an unregistered server, not as a project failure.

Require Python 3.12 or newer. Create or update the environment as needed:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip check
```

Do not recreate a healthy existing virtual environment unnecessarily.

## 2. Configure the Bungie application

Open the Bungie Developer Portal and configure one application with:

- OAuth client type: `Confidential`. This implementation requires a client secret for authorization-code exchange and token refresh.
- Redirect URL: `https://localhost:8765/callback`.
- Required values: API Key, OAuth client ID, and OAuth client secret.

Keep these concepts separate:

- `https://www.bungie.net/en/OAuth/Authorize` is only the base authorization endpoint. It is not a client secret and is not a complete login link.
- The OAuth helper generates the complete authorization URL with `client_id`, `response_type`, `state`, and the encoded redirect URI.
- `https://localhost:8765/callback` is not a login page. It only receives the Bungie redirect while the local helper is running.
- The client secret comes from the application configuration page, not from the user's Bungie login.

If the portal does not show a client secret, verify that the app is `Confidential`. Do not silently adapt this project to a Public client; that requires a separate end-to-end OAuth and refresh-token implementation change.

## 3. Create the local environment file

If `.env` is absent, create it from `.env.example` without modifying `.env.example` with real values. Keep this shape:

```dotenv
BUNGIE_API_KEY=
BUNGIE_CLIENT_ID=
BUNGIE_CLIENT_SECRET=
DESTINY_MCP_TOOL_PROFILE=normal
DESTINY_OAUTH_REDIRECT_URI=https://localhost:8765/callback
```

Have the user fill the three credentials locally. Then secure and verify the file without revealing values:

```bash
chmod 600 .env
git check-ignore -v .env
awk -F= '/^(BUNGIE_API_KEY|BUNGIE_CLIENT_ID|BUNGIE_CLIENT_SECRET|DESTINY_OAUTH_REDIRECT_URI)=/{v=substr($0,index($0,"=")+1); print $1 "=" (length(v) ? "set" : "missing")}' .env
```

All four fields must report `set`. Keep secrets in `.env`; do not duplicate them in `~/.codex/config.toml`.

## 4. Complete OAuth login

Start the helper and keep its process running while the user logs in:

```bash
.venv/bin/destiny-mcp-oauth --no-open --timeout 900
```

Relay the complete generated Bungie URL to the user. The user must open that URL, log in, approve access, and allow the browser to continue to the HTTPS localhost callback.

The callback helper creates a temporary self-signed certificate for `localhost`. If the browser reports an untrusted local certificate, instruct the user to use the browser's advanced option to continue. Do not confuse a certificate warning with `ERR_CONNECTION_REFUSED`.

Successful output must include:

- `Bungie 登录完成`
- a saved token path under `~/.destiny_mcp/tokens.json` by default
- a refresh token marked as present

If the automatic callback fails but the browser address contains `code=...`, immediately run:

```bash
.venv/bin/destiny-mcp-oauth --manual
```

Have the user paste the full callback URL into the local terminal prompt, not into chat. Authorization codes are short-lived and should not be logged.

If changing the callback port, update both the Bungie application and `DESTINY_OAUTH_REDIRECT_URI`. When that environment variable is set, do not assume `--port` overrides it.

## 5. Register the stdio server with the host that is running you

**Register into the agent the user is actually talking to, not into every agent on the machine.**
Ask which host this is if the environment does not say so; DSH sets `DSH_HOME`, Claude Code sets
`CLAUDECODE`, Codex sets `CODEX_*`. `scripts/install_skill.py --list` prints the host it detected
and where it would write; `--host <name>` overrides it.

For DeepSeek Harness (DSH), the registration is a profile patch and the installer can write it
idempotently:

```bash
# once per profile, if the MCP client plugin is missing
(cd "$DSH_HOME/profiles/web" && pnpm add @deepseek-ai/dsh-mcp-client)
# idempotent: inserts or refreshes one marked entry, backs the file up first
.venv/bin/python scripts/install_skill.py --mcp
```

The entry points at this checkout's `.venv/bin/destiny-mcp` with `DESTINY_MCP_ROOT`,
`DESTINY_MCP_TOOL_PROFILE=normal` and `toolCallTimeoutMs: 180000` (the 60s default is too short for
the first Manifest load). Tools appear as `mcp__destiny__*` after the harness reloads. The skill
half needs no restart: DSH discovers `~/.dsh/skills/` live.

For hosts with their own CLI, the installer prints the exact command instead of editing their
configuration behind the user's back:

```bash
.venv/bin/python scripts/install_skill.py --host claude --mcp   # prints a `claude mcp add ...` line
.venv/bin/python scripts/install_skill.py --host codex --mcp    # prints a `codex mcp add ...` line
```

### Codex, in detail

Inspect an existing entry first:

```bash
codex mcp get destiny
```

If no entry exists, request permission to update the user's global Codex configuration and run the following with the checkout's literal absolute path:

```bash
codex mcp add destiny --env 'DESTINY_MCP_ROOT=/absolute/path/to/destiny2-mcp' --env DESTINY_MCP_TOOL_PROFILE=normal -- '/absolute/path/to/destiny2-mcp/.venv/bin/destiny-mcp'
```

Use `DESTINY_MCP_ROOT` because `codex mcp add` may not provide a working-directory option. Keep the command and project root absolute. Do not add Bungie credentials to the Codex entry; the server loads the checkout's `.env`.

If an entry exists with a different checkout or executable, report the mismatch and get approval before removing or replacing it.

Verify registration:

```bash
codex mcp get destiny
codex mcp list
```

The entry must be enabled, use stdio, and point to the expected `.venv/bin/destiny-mcp`.

## 6. Verify a real MCP startup

Before the first handshake, decide whether the Manifest databases are already present:

```bash
ls manifest/*.sqlite3 2>/dev/null || echo "MANIFEST_MISSING"
```

`MANIFEST_MISSING` is not a failure, but the first startup then downloads about **717 MB** from Bungie and builds both indexes before answering any tool call — tens of minutes on a normal connection. Prefer fetching the prebuilt pair first, and only run the verifier after they exist:

```bash
mkdir -p manifest
gh release download manifest-data-v1 -R 8z2wz746tv-png/destiny2-mcp -D manifest
```

Run the bundled verifier after OAuth succeeds:

```bash
.venv/bin/python skills/destiny-mcp-setup/scripts/verify_mcp.py
```

The verifier raises its own timeout to 1800s and prints `MANIFEST=missing ...` when the databases are absent; keep it running instead of treating the wait as a hang. On a slow link, or with an older copy of the script, pass `--timeout 1800` explicitly.

Allow Bungie network and token-file access when the sandbox requires approval. The verifier starts the same stdio command, initializes an MCP client session, calls the read-only `player_assistant(intent="profile")`, and then calls `list_tools` without printing secrets or profile data.

For the default `normal` profile, require exactly these eight tools:

```text
player_assistant
inventory_assistant
weapon_assistant
build_assistant
loadout_assistant
subclass_assistant
activity_assistant
world_assistant
```

After a successful handshake, tell the user to restart Codex or open a new task because the current task may not dynamically load a newly registered MCP server.

## Troubleshoot by symptom

### `ERR_CONNECTION_REFUSED` at the callback

Assume no process is listening. Do not refresh a stale callback page.

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
.venv/bin/destiny-mcp-oauth --no-open --timeout 900
```

Inspect the OAuth command's terminal output. A common cause is an empty `BUNGIE_CLIENT_SECRET`, which makes the helper exit before binding the port. Other causes include a port conflict, an expired waiting window, or opening the callback directly instead of the generated authorization URL.

### Browser certificate warning

Confirm the URL is exactly `https://localhost:<port>/callback` and the OAuth helper is running. Continue past the temporary localhost certificate warning. Never replace `localhost` with `127.0.0.1`; Bungie rejects the numeric host for this application flow.

The helper creates its temporary certificate with the `openssl` command-line tool. If `openssl` is missing (common on Windows), it stops with a message telling you to use the manual path instead of a traceback:

```bash
.venv/bin/destiny-mcp-oauth --manual
```

### First verification times out

A cold start downloads and indexes about 717 MB of Manifest data before the server answers. Leave it running; the verifier raises its own timeout to 1800s and prints `MANIFEST=missing ...` when this is the reason. With an older copy of the verifier, pass `--timeout 1800`. Fetching the prebuilt `manifest-data-v1` pair first avoids the wait entirely.

### Missing or invalid state

Restart the OAuth helper and use only the newly generated authorization URL. Do not reuse an old URL after the helper times out or restarts.

### Missing token or refresh failure

Confirm `~/.destiny_mcp/tokens.json` exists without printing it. Re-run OAuth if it is absent or invalid. Keep the same application's client ID and client secret in `.env`; refresh requires them.

### MCP is listed but unavailable in Codex

Verify a real handshake with `verify_mcp.py`, then restart Codex or create a new task. Registration alone does not prove that credentials, tokens, manifest loading, or server lifespan initialization work.

## Completion criteria

Finish only when all conditions hold:

1. Dependencies pass `pip check`.
2. `.env` contains all required values, is ignored, and is not printed.
3. OAuth saves a refresh token.
4. `codex mcp get destiny` reports an enabled stdio entry with the expected absolute command.
5. `verify_mcp.py` reports `BUNGIE_PROFILE_CHECK=ok` and all eight normal-profile tools.
6. The user is told to restart Codex or use a new task.
