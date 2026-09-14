# Dashboard health

Dashboard health lets a user confirm the local operations console HTTP API is running and can read the verification database.

## Sub-features

- `health-endpoint` returns API readiness.
- `overview-counts` returns session and knowledge summary counts.

## How to get to it (user POV)

- Start the dashboard with `lucid-memories dashboard` (or `open`).
- Open `http://127.0.0.1:<port>/api/v1/health` in a browser or HTTP client.
- Open `http://127.0.0.1:<port>/api/v1/overview`.

## Driving it with control-lucid

Preconditions:

- `control-lucid env-init` completed.
- `control-lucid dashboard-start` reports `dashboard ready`.
- Verification uses port `18765` by default to avoid colliding with a user dashboard on `8765`.

- **Health.** Run `control-lucid api-get health`. Exit code `0` and body JSON contain `"ok": true`.
- **Overview.** Run `control-lucid api-get overview`. Exit code `0` and body JSON contain a `counts` object.
- **Proof.**

```bash
ART="$(control-lucid artifacts-dir)"
control-lucid api-get health | tee "$ART/dashboard-health.json"
control-lucid api-get overview | tee "$ART/dashboard-overview.json"
printf 'feature=dashboard-health entry=api-health\n' >"$ART/proof.txt"
```

## Gotchas

- The React UI requires `npm --prefix src/lucid_memories/web/frontend run build`. This feature verifies the HTTP API only.
- Always `dashboard-stop` using the PID file; never kill unrelated processes on port 8765.
- Point `LUCID_MEMORIES_DB` at the disposable home (handled automatically when `LUCID_MEMORIES_HOME` is set).
