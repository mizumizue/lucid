---
name: lucid-memories
description: >-
  lucid — Cursor agents' shared SQLite store and Map. Call whoami and status
  once at session start. Recall only when the instruction type changes. Remember
  facts and propose links; ask only for confirm, forbid, or archive.
  Use when watching other chats' jobs, reloading after compact, or pulling
  context without a SubAgent.
---

# lucid

Agent 間の共有ストア。会話履歴の複製ではない。CLI または MCP `lucid-memories` を使い、生 SQL / 生 Cypher は書かない。

コマンド正本: `python ~/.cursor/lucid-memories/cli.py <cmd>`。MCP ツール名は CLI と同じ（`whoami` `status` `search` `vsearch` `list` `load` `remember` `archive` `job` `reload` `relay` `recall` `link` `forbid` `confirm` `map` `measure` `embedding_status`）。

## 先導語

**lucid** — このセッションが知っていることを、他セッションが必要時に小さく載せる場所。
**Map** — 指示・処理・資料を、話題・文脈・関連の意味で結ぶ。本文は lucid、関係は Ladybug。
**パイプライン** — ユーザー指示が回ったら、検索と提案書き込みは Agent が自動で行う。ユーザー承認は確定操作だけ。

## 手順

### 1. 自分を束ねる

作業の最初のターンで `whoami` と `status` を一度呼ぶ。`--workspace` は省略時 cwd。`--session` が分かるなら付ける。

**完了:** `conversation_id` が分かっている。running jobs / unread notices / `reload_available` を見ている。

### 2. 指示パイプライン

セッション開始の whoami / status のあと、今の指示の型が直前と違うときだけ `recall`。他セッションの断片が要るときだけ `search` / `vsearch` / `load`。hook の digest があればその hits を使う。日付は順位だけ。

recall が見てよい辺は confirmed、または `count >= 3` かつ `last_at` が 14 日以内。denied / FORBIDS は隠す。本文は SQLite、予算は既定 2000。recall しただけで `link` の回数は増えない。使った辺と使わなかった辺を区別する。

digest は欠け（empty / sqlite_only / map_without_procedure / procedure_without_material）をログする。digest から自動 `link` はしない。バックフィルは人が `measure --improve` するときだけ。集計は `measure`、既知ケースは `measure --eval`。

このターンで残す事実はすぐ `remember`。残す関係はすぐ `link`（proposed）。承認ダイアログを待たない。使ったからリンクしない。話題は `--rel ABOUT`、文脈は `--rel IN_CONTEXT`、それ以外は `--rel RELATED --sense related --label "..."`。指示→処理は `--rel TRIGGERS`。資料は `--rel USES`。`link` に `--confirm` は付けない。プロンプト全文を Procedure / Directive 名にしない。

確定はユーザーに聞いてから `confirm`。禁止は聞いてから `forbid --from ... --to ... --reason "..."`。知識を隠すのは聞いてから `archive --id`。

`status.reload_available` が true なら `reload`。長時間仕事は `job start`、終わったら `job done --id --rev`。

Knowledge は既定で Ollama の `nomic-embed-text` による 768 次元の float32 ベクトルとして SQLite に保存される。ユーザー指示の digest は FTS / Map に加えて `vsearch` の意味検索を自動利用するため、通常の引き継ぎで `relay save` は不要。初回導入後に既存 Knowledge を埋める場合は `python ~/.cursor/lucid-memories/cli.py embed backfill`、状態確認は `embed status`。モデルは `LUCID_MEMORIES_EMBED_MODEL`、Ollama URL は `LUCID_MEMORIES_OLLAMA_URL` で変更できる。

**完了:** 指示の型が変わったなら recall 済み。digest または追加 search の該当 hits を使っている。使わなかった辺の count は増えていない。残す事実は lucid 上にあり、confirm / forbid / archive はユーザーが認めたものだけ。自分が始めた Job は終端 status になっている。

## リファレンス

- Session / Job / Knowledge / Pack / Lease / Notice の語は lucid の用語。Map の語は Directive / Procedure / Material / Topic / Context。辺の意味は topic / context / related。Map ノードを memory や曖昧な context とは言わない（文脈ノードは Context）。
- compact は Cursor が行う。lucid は `preCompact` で Pack を残し、こちらは `reload` する。
- スレッドの引き継ぎは `relay save` / `relay load`（Pack `kind=handoff`）。`reload` は compact 用で、relay ではない。
- 衝突する Job を取るときは `job claim --id --rev`。rev が食い違ったら `status` し直す。
- 公式 Ladybug MCP は使わない。charactor の `lines.lbdb` とは別ファイル（`map.lbdb`）。
- 検索・remember・proposed link は Auto。confirm / forbid / archive だけ Ask。
- 指示ごとの search/recall は `retrieval_logs` に残る。digest が評価して欠けをログする。集計は `measure`、既知ケースは `--eval`、過去ログの埋めは人が `--improve`。
