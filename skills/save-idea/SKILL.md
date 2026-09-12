---
name: save-idea
description: >-
  idea を lucid-memories に残す。ユーザーがメモ, アイデア, 実装アイデア, 貯める,
  溜める, アイデアを残す, アイデアを保存, メモを残す, メモを保存,
  思いつき, 後で実装 と言ったとき、または保存したメモの一覧・検索・追記のとき使う。
---

# idea

実装アイデアとメモの正本は **lucid**（`kind=idea`）。Markdown ファイルには書かない。

コマンド: `python ~/.cursor/lucid-memories/cli.py`。MCP `lucid-memories` でも同じ。

## 手順

### 1. 書く

要点を本文にまとめる。秘密情報は入れない。入っていたら警告して伏せる。

Home / 横断なら `--scope global`。プロジェクト作業なら `--scope workspace --workspace <root>`。

```text
python ~/.cursor/lucid-memories/cli.py remember --kind idea --scope global --title "タイトル" --body "本文" --tags "tag1,tag2"
```

長い本文は `--body-file`。

**完了:** `ok: true` と `id` が返り、その id をユーザーに伝えている。ファイルパスは出さない。

### 2. 一覧・検索

- 一覧: `python ~/.cursor/lucid-memories/cli.py list --kind idea --all`
- 検索: `python ~/.cursor/lucid-memories/cli.py search "語句" --kind idea`
- 本文: `python ~/.cursor/lucid-memories/cli.py load <id>`

**完了:** 該当 idea の id・タイトル・概要を返している。

### 3. 追記

既存を読んでから `remember --id <id> --rev <rev> --body "..."`（または `--body-file`）。rev が食い違ったら `load` し直す。新規は常に新しい `remember`（既存 id を上書きしない）。

**完了:** 更新後の id と rev を伝えている。

## 本文の型

```markdown
# タイトル

## 概要

1〜3文。

## 背景・きっかけ

## 実装案

- 

## メモ

```

短いメモなら概要とメモだけでよい。
