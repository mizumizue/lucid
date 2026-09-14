# lucid-memories verification map

Maintained source for verifying user-facing behavior of lucid-memories. Read this index before driving the app, then open the matching feature file.

## Baseline preconditions

- Run from the repository root with `bin/lucid-memories` present.
- Set a unique `RUN_ID` and call `control-lucid env-init` so `LUCID_MEMORIES_HOME` points at a disposable directory.
- Use the verify workspace and session baked into `control-lucid` (`VERIFY_WORKSPACE`, `VERIFY_SESSION`).
- Put `.cursor/skills/verify-lucid-memories/scripts/control-lucid` on your path or invoke it with a repo-relative path.
- Run `control-lucid doctor` and require `cli: ok` and `persona: ok`.
- Never drive the live `~/.cursor/lucid-memories` instance during verification.

## Driving conventions

- Start every recipe from the baseline unless its preconditions say otherwise.
- Run terminal actions through `control-lucid cli -- <command>`.
- Run dashboard HTTP checks through `control-lucid api-get <resource>`.
- Treat every command as literal. Keep quoted titles and flags unchanged.
- Restore seeded data only inside the disposable home. Do not remove proof artifacts during cleanup.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final JSON field.
- CLI proof includes the command, stdout, stderr, and exit code.
- API proof includes the response body and HTTP status.
- Mutation proof includes a read-only second view (`search`, `list`, or `api-get knowledge/<id>`).
- Record the feature ID and entry point in `proof.txt` beside artifacts.
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing user-visible behavior. It then uses exactly four H2 sections in this order:

1. `Sub-features`
2. `How to get to it (user POV)`
3. `Driving it with control-lucid`
4. `Gotchas`

## Features

- [Remember and search](./remember-and-search.md) — save a memory from the CLI and find it with keyword search.
- [Status and whoami](./status-and-whoami.md) — inspect session and system health from the CLI.
- [Persona validate](./persona-validate.md) — confirm global persona files render within token budget.
- [Dashboard health](./dashboard-health.md) — dashboard HTTP API reports database health and overview counts.
