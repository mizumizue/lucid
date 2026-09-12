---
name: relay
description: >-
  relay — create an explicit lucid-memories handoff Pack for long or curated
  transfers. Normal cross-session context is retrieved automatically by the
  lucid-memories embedding index; use this only when an explicit Pack is requested.
argument-hint: "次のセッションは何に使いますか？"
---

# relay

通常の会話のバトンは **lucid** の Knowledge と埋め込み検索が担う。ユーザーが明示的に Pack を固定したい場合だけ、会話のバトンを **lucid** の Pack（`kind=handoff`）として保存する。ファイルには書かない。

`python ~/.cursor/lucid-memories/cli.py relay …` または MCP `relay`。

## 先導語

**relay** — 明示的に固定したい引き継ぎだけを Pack にし、通常は `vsearch` が必要分を拾う。

## 手順

### 1. 渡す（save）

ユーザー引数があれば次セッションの**焦点**にする。本文は会話から圧縮する。既に仕様・計画・ADR・issue・コミット・diff にある内容は重複させずパスか URL で指す。機密は伏せ字。`suggested skills` に、受け手が起動すべきスキルを列挙する。

Home / 横断は `--scope global`。プロジェクトは `--scope workspace`。

```text
python ~/.cursor/lucid-memories/cli.py relay save --title "短い題" --focus "次にやること" --skills "lucid-memories,tdd" --body "本文"
```

長い本文は `--body-file`。

**完了:** `ok` と `pack_id` をユーザーに伝え、次チャットで `relay load`（または `--id <pack_id>`）するよう案内している。ファイルパスは出さない。

### 2. 受け取る（load）

新しい会話で引き継ぎが要るとき、先に `relay load`。id が分かれば `--id`。焦点の語句があれば `--query`。どちらも無ければ同一 workspace の最新 Pack。

```text
python ~/.cursor/lucid-memories/cli.py relay load
python ~/.cursor/lucid-memories/cli.py relay load --id <pack_id>
```

返った本文と suggested skills をこのセッションの前提にする。omitted があれば必要な ID だけ `load`。

**完了:** Pack 本文がコンテキストに載り、suggested skills を把握している。会話ログは全文読んでいない。

## 本文の型

```markdown
# 題

## 焦点

次セッションがやること。

## 状態

今どこまで終わっているか。残作業。

## ポインタ

既出アーティファクトのパスまたは URL。

## suggested skills

- skill-name
```
