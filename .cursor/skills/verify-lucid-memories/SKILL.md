---
name: verify-lucid-memories
description: Drive lucid-memories the way a user does — isolated CLI and dashboard API against a disposable LUCID_MEMORIES_HOME. Use after behavior changes to remember/search, status, persona injection, or dashboard health.
---

# Verify lucid-memories

Prove lucid-memories behavior by driving the real CLI and dashboard HTTP API against a disposable data directory. Never touch the user's live `~/.cursor/lucid-memories` instance unless they explicitly opt in.

Read `.cursor/skills/verify-lucid-memories/features/README.md` before driving. Use the matching feature file as the recipe.

## Launch

From the repository root:

```bash
export RUN_ID="lucid-verify-$(date +%Y%m%d%H%M%S)-$$"
CONTROL=".cursor/skills/verify-lucid-memories/scripts/control-lucid"
chmod +x "$CONTROL"
"$CONTROL" env-init
```

Ready when `env-init` prints a `LUCID_MEMORIES_HOME` path and `control-lucid doctor` reports `cli: ok` and `persona: ok`.

For dashboard recipes, start after `env-init`:

```bash
"$CONTROL" dashboard-start
```

Ready when `dashboard-start` prints `dashboard ready` and `api-get health` returns JSON with `"ok": true`.

**Teardown** (always run after a verification attempt, success or failure):

```bash
"$CONTROL" dashboard-stop
rm -rf "$( "$CONTROL" state-dir )" "$( "$CONTROL" home-dir )"
```

Do not delete `"$( "$CONTROL" artifacts-dir )"` during teardown.

## Doctor

Run first whenever anything looks off:

```bash
.cursor/skills/verify-lucid-memories/scripts/control-lucid doctor
```

Expect: `cli: ok`, `persona: ok`. Dashboard lines depend on whether `dashboard-start` was used. Exit code `0` means the instance is worth driving.

Never drive an instance you did not start in this run (no shared `~/.cursor/lucid-memories`).

## Drive

Harness: `control-lucid` wrapper around `bin/lucid-memories` with fixed `--workspace` and `--session`, plus optional dashboard HTTP on port `18765` (override with `LUCID_MEMORIES_DASHBOARD_PORT`).

| Action | Command |
|---|---|
| CLI | `control-lucid cli -- <subcommand> [args]` |
| Dashboard API | `control-lucid api-get <resource>` (e.g. `health`, `overview`, `knowledge`) |
| Browser UI | Only after `npm --prefix src/lucid_memories/web/frontend run build`; then open `http://127.0.0.1:18765/` |

Prefer stable JSON fields (`ok`, `id`, `title`, `hits`) over log wording.

## Evidence

Write proof to `test-results/verify-lucid-memories/<RUN_ID>/` (printed by `control-lucid artifacts-dir`).

Standards:

- Exercise the real user path (CLI `remember` / `search`, not direct SQL).
- Capture command + stdout + exit code for CLI proof.
- Capture API response body for dashboard proof.
- Re-read state with a second command (`list`, `search`, `api-get knowledge/<id>`) — not just the write response.
- Record feature ID and entry point in a one-line `proof.txt` beside artifacts.

Example:

```bash
ART="$(control-lucid artifacts-dir)"
control-lucid cli -- remember --title "Verify memory" --body "Created by verify-lucid-memories" --kind fact --scope workspace \
  | tee "$ART/remember.json"
control-lucid cli -- search "Verify memory" | tee "$ART/search.json"
printf 'feature=remember-and-search entry=cli-remember\n' >"$ART/proof.txt"
```

## Cleanup

1. `control-lucid dashboard-stop` — kills only the PID this run recorded.
2. `rm -rf "$(control-lucid state-dir)" "$(control-lucid home-dir)"` — removes disposable DB and persona.
3. Confirm artifacts still exist under `test-results/verify-lucid-memories/<RUN_ID>/`.

Never `pkill lucid` or `killall python`. Never remove the user's live home directory.

## Helpers

All helpers live in `.cursor/skills/verify-lucid-memories/scripts/control-lucid` (executable).

```bash
control-lucid env-init           # isolated home + seeded persona
control-lucid doctor             # read-only health
control-lucid cli -- status      # lucid-memories with verify env
control-lucid dashboard-start    # background dashboard on 127.0.0.1:18765
control-lucid dashboard-stop
control-lucid api-get health
control-lucid artifacts-dir
control-lucid home-dir
```

Isolation uses `LUCID_MEMORIES_HOME=/tmp/lucid-verify-<RUN_ID>` (or `$TMPDIR` equivalent). Two runs can run side by side when `RUN_ID` differs.

Keep feature recipes in `.cursor/skills/verify-lucid-memories/features/`. Update them with `/maintain-verification-skill` when behavior changes.
