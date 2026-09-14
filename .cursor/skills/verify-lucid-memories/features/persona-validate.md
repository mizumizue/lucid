# Persona validate

Persona validate lets a user confirm that global persona files exist, render within the token budget, and produce injectable content.

## Sub-features

- `validate-ok` reports schema, section count, and token estimate.
- `show-injection` renders the `[global persona]` header and section bodies.

## How to get to it (user POV)

- Run `lucid-memories persona validate`.
- Run `lucid-memories persona show`.

## Driving it with control-lucid

Preconditions:

- `control-lucid env-init` completed (seeds `user-rules.json` and `persona.json` under the disposable home).
- `control-lucid doctor` reports `persona: ok`.

- **Validate.** Run `control-lucid cli -- persona validate`. Exit code `0` and stdout JSON contain `"ok": true`, `"sections"` ≥ 1, and `"over_budget": false`.
- **Show injection.** Run `control-lucid cli -- persona show`. Exit code `0` and stdout JSON `injection.content` contains `[global persona]`.
- **Proof.**

```bash
ART="$(control-lucid artifacts-dir)"
control-lucid cli -- persona validate | tee "$ART/persona-validate.json"
control-lucid cli -- persona show | tee "$ART/persona-show.json"
printf 'feature=persona-validate entry=cli-persona-validate\n' >"$ART/proof.txt"
```

## Gotchas

- Validation reads `LUCID_MEMORIES_HOME/persona/persona.json`, not Cursor User Rules directly.
- `env-init` must run before validate on a fresh disposable home.
- Token budget omissions (`over_budget: true`) fail this feature even when `ok` is true.
