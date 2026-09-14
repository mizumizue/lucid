# Status and whoami

Status and whoami let a user inspect the current lucid-memories session identity and overall system health from the terminal.

## Sub-features

- `whoami-session` resolves workspace and session context.
- `status-health` reports jobs, notices, and embedding status fields.

## How to get to it (user POV)

- Run `lucid-memories whoami`.
- Run `lucid-memories status`.

## Driving it with control-lucid

Preconditions:

- `control-lucid env-init` completed.
- `control-lucid doctor` reports `cli: ok`.

- **Whoami.** Run `control-lucid cli -- whoami`. Exit code `0` and stdout JSON contain `"ok": true`.
- **Status.** Run `control-lucid cli -- status`. Exit code `0` and stdout JSON contain `"ok": true` and a `self` object.
- **Proof.**

```bash
ART="$(control-lucid artifacts-dir)"
control-lucid cli -- whoami | tee "$ART/whoami.json"
control-lucid cli -- status | tee "$ART/status.json"
printf 'feature=status-and-whoami entry=cli-status\n' >"$ART/proof.txt"
```

## Gotchas

- Fresh verify homes start with empty session lists; absence of sessions is normal.
- Do not treat MCP `whoami` output as a substitute when this feature targets the CLI entry point.
