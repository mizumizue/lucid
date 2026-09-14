# Remember and search

Remember and search lets a user save a titled memory from the terminal and retrieve it later with keyword search in the same workspace.

## Sub-features

- `remember-save` persists a title, body, and kind.
- `search-hit` finds the saved memory by keyword.
- `search-miss` returns no hits for unrelated queries.

## How to get to it (user POV)

- Run `lucid-memories remember --title <title> --body <body> --kind <kind> --scope workspace`.
- Run `lucid-memories search <query>` in the same workspace.

## Driving it with control-lucid

Preconditions:

- `control-lucid env-init` completed.
- `control-lucid doctor` reports `cli: ok`.
- No knowledge item is titled `Verify memory alpha`.

- **Save memory.** Run `control-lucid cli -- remember --title "Verify memory alpha" --body "Saved during verify-lucid-memories" --kind fact --scope workspace`. Exit code `0` and stdout JSON contain `"ok": true` and an `"id"`.
- **Confirm via search.** Run `control-lucid cli -- search "Verify memory alpha"`. Exit code `0` and stdout JSON list at least one hit whose `title` is `Verify memory alpha`.
- **Confirm negative search.** Run `control-lucid cli -- search "nonexistent-verify-token"`. Exit code `0` and stdout JSON show zero hits (or an empty `hits` / `results` list).
- **Proof.** Save stdout to artifacts:

```bash
ART="$(control-lucid artifacts-dir)"
control-lucid cli -- remember --title "Verify memory alpha" --body "Saved during verify-lucid-memories" --kind fact --scope workspace | tee "$ART/remember.json"
control-lucid cli -- search "Verify memory alpha" | tee "$ART/search-hit.json"
control-lucid cli -- search "nonexistent-verify-token" | tee "$ART/search-miss.json"
printf 'feature=remember-and-search entry=cli-remember\n' >"$ART/proof.txt"
```

## Gotchas

- `remember` without `--scope workspace` may store under a different scope; keep `--scope workspace` for this recipe.
- Search is workspace-scoped. Always use `control-lucid cli` so `--workspace` stays consistent.
- A successful `remember` response alone is insufficient proof. Always run `search` afterward.
