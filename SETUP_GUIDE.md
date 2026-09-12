# セットアップガイド (Setup Guide)

本書は、新規マシンや別 PC、または初期化された環境へ **lucid-memories** をセットアップ・再構築するための完全な手順書です。

付属の自動化スクリプト、または AI エージェント用スキルを利用することで、数分で Cursor と連携した永続記憶・ナレッジグラフ環境を復元できます。

---

## 目次
1. [アーキテクチャと連携概要](#1-アーキテクチャと連携概要)
2. [動作要件・前提条件](#2-動作要件前提条件)
3. [クイックスタート（ワンライナー実行）](#3-クイックスタートワンライナー実行)
4. [AI エージェントによる自律再構築](#4-ai-エージェントによる自律再構築)
5. [セットアップスクリプトが自動実行する内容](#5-セットアップスクリプトが自動実行する内容)
6. [ベクトル検索（Ollama）のセットアップ（任意）](#6-ベクトル検索ollamaのセットアップ任意)
7. [動作確認と健全性テスト](#7-動作確認と健全性テスト)
8. [トラブルシューティング](#8-トラブルシューティング)
9. [環境のリセットと再初期化](#9-環境のリセットと再初期化)

---

## 1. アーキテクチャと連携概要

`lucid-memories` は Cursor の AI エージェント同士がセッションやワークスペースをまたいで知識を共有するためのシステムです。以下の構成要素が Cursor と有機的に連携します。

```text
Cursor IDE
├── MCP Client  ────────▶  [MCP Server] src/lucid_memories/entrypoints/mcp_server.py
│                          (ツール呼び出し: whoami, status, search, remember, link 等)
│
├── Cursor Hooks ───────▶  [Hook Handler] src/lucid_memories/entrypoints/hook.py
│                          (セッション開始、プロンプト送信、ツール実行時の自動記憶想起・ログ記録)
│
└── Skills (~/.cursor/skills/)
    ├── lucid-memories   (基本操作スキル)
    ├── relay            (明示的なセッション間引き継ぎ Pack)
    ├── save-idea        (アイデア・メモ永続化)
    └── setup-lucid-memories (環境再構築スキル)

ローカルストレージ (~/.cursor/lucid-memories/.db/)
├── lucid-memories.sqlite (知識ストア・FTS5 全文検索・ベクトルインデックス)
└── map.lbdb             (Ladybug グラフDB: Directive/Procedure/Material/Topic/Context)
```

---

## 2. 動作要件・前提条件

- **OS**: Windows 10/11 (64bit), macOS (Apple Silicon / Intel), Linux (x86_64 / arm64)
- **Python**: **3.10 以上** (3.11〜3.14 推奨)
  - Windows の場合は `python.exe` が PATH に通っているか、標準の AppData パスにインストールされていること。
  - CPython 標準の `sqlite3` モジュールが利用可能であること。
- **Git**: リポジトリのクローン用
- **Cursor**: 最新バージョン（MCP および Hooks 機能をサポート）
- **Ollama** (任意・推奨):
  - 意味的ベクトル検索（`vsearch`）を利用する場合に必要。
  - 未導入の場合でも SQLite FTS5 による高速全文検索が自動フォールバックとして動作します。

---

## 3. クイックスタート（ワンライナー実行）

### ステップ 1: リポジトリのクローン
推奨配置先は `~/.cursor/lucid-memories` です。

```bash
# macOS / Linux / Git Bash
git clone <repository-url> ~/.cursor/lucid-memories
cd ~/.cursor/lucid-memories
```

Windows PowerShell の場合:
```powershell
git clone <repository-url> "$env:USERPROFILE\.cursor\lucid-memories"
Set-Location "$env:USERPROFILE\.cursor\lucid-memories"
```

### ステップ 2: セットアップランナーの実行
お使いの環境に応じて以下のコマンドを 1 行実行します。

- **macOS / Linux (POSIX bash)**:
  ```bash
  ./skills/setup-lucid-memories/scripts/setup.sh
  ```
- **Windows (PowerShell)**:
  ```powershell
  .\skills\setup-lucid-memories\scripts\setup.ps1
  ```
- **クロスプラットフォーム直接実行**:
  ```bash
  python skills/setup-lucid-memories/scripts/setup.py
  ```

※ 仮想環境や特定の Python インタプリタを使用したい場合は `--python` オプションを指定できます:
```bash
python skills/setup-lucid-memories/scripts/setup.py --python "/path/to/venv/bin/python"
```

### ステップ 3: Cursor の再起動
設定を Cursor に反映させるため、Cursor を再起動するか、コマンドパレット（`Ctrl+Shift+P` / `Cmd+Shift+P`）から **`Developer: Reload Window`** を実行してください。

---

## 4. AI エージェントによる自律再構築

Cursor 内で開いたチャットから、付属のスキル `setup-lucid-memories` を呼び出すことでも環境を再構築できます。

### 実行方法
チャット入力欄に以下のように指示します:

> 「setup-lucid-memories スキルを実行して環境を再構築して」

### エージェントの実行フロー
1. **Probe（環境調査）**: Python バージョン、OS、既存の `mcp.json` / `hooks.json`、Ollama 接続状況を非破壊調査。
2. **Wire（結合配線）**: 依存パッケージインストール、MCP/Hooks/Skills/Persona の設定マージを一括処理。
3. **Verify（健全性検証）**: `cli status` とテストスイートを実行し、DB 自動生成・マイグレーション・応答性を検証。
4. **Guide（完了案内）**: Cursor の再読み込みと Ollama の稼働状態をユーザーに報告。

---

## 5. セットアップスクリプトが自動実行する内容

`skills/setup-lucid-memories/scripts/setup.py`（および各ランナー）は以下のステップを自動的・決定論的に実行します。

1. **Python バージョン検査**:
   - `sys.version_info >= (3, 10)` を検証します。
2. **依存関係のインストール**:
   - `pip install -e .` により Python パッケージ（および必須依存 `ladybug>=0.20.0`）をインストールします。
3. **ディレクトリ構造の生成**:
   - `~/.cursor/lucid-memories/.db/`（データベース用: SQLite, Ladybug）
   - `~/.cursor/lucid-memories/persona/`（ユーザー固有ペルソナ・ルール設定用）
   - `~/.cursor/hooks/`（フックスクリプト用）
   - `~/.cursor/skills/`（個人スキル用）
   ※ BLOB データおよび実行ログは SQLite データベース（`.db/` 内の `blobs`, `runtime_logs` テーブル）に内包されるため、独立した `blobs/` や `logs/` ディレクトリは自動生成されません（旧バージョンの空ディレクトリが存在する場合はセットアップ時に自動除去されます）。
4. **Cursor MCP の設定 (`~/.cursor/mcp.json`)**:
   - 既存の `mcp.json` があれば読み込み、他の MCP サーバー設定を保持したまま `lucid-memories` を登録・更新します。
   - 構文エラーがあった場合は自動で `.json.bak` を作成して退避します。
5. **Cursor Hooks の設定 (`~/.cursor/hooks.json`)**:
   - OS 別フックスクリプト（Windows: `lucid-memories.cmd`, POSIX: `lucid-memories.sh`）を生成します。スクリプトはエラー時でも空 JSON `{}` を返却するフェイルセーフ仕様です。
   - `hooks.json` 内の全 17 イベント（`sessionStart`, `beforeSubmitPrompt`, `preToolUse`, `postToolUse`, `preCompact` 等）にフックをマージします。
6. **Cursor Skills の配置 (`~/.cursor/skills/`)**:
   - リポジトリの `skills/` にバンドルされたスキル群を `~/.cursor/skills/` 配下に展開します。
     - `lucid-memories`: 基本操作
     - `relay`: セッション間引き継ぎ Pack
     - `save-idea`: アイデア・メモ永続化
     - `setup-lucid-memories`: 環境再構築
7. **Persona 設定の自動初期化 (`persona/`)**:
   - `persona/` ディレクトリはユーザー固有設定として `.gitignore` されており、リポジトリには含まれません。
   - スクリプト実行時に `persona/user-rules.json` および `persona/persona.json` が正規スキーマ（`schema_version: 1`, `token_budget: 8000`, `sections`）で自動生成されます。
   - 既存のファイルが存在する場合は上書きせず保持されるため、ユーザー独自の方針設定が安全に保護されます。
8. **データベース初期化と自己診断 (Self-Test)**:
   - `python src/lucid_memories/entrypoints/cli.py status` を実行し、SQLite（`lucid-memories.sqlite`）および Ladybug（`map.lbdb`）のスキーマ適用と `ok: true` の返却を確認します。
   - `python src/lucid_memories/entrypoints/cli.py persona validate` を実行し、初期生成されたペルソナが正常に認識されることを検証します。

---

## 6. ベクトル検索（Ollama）のセットアップ（任意）

`lucid-memories` は意味的類似度検索（`vsearch`）に **Ollama** と **`nomic-embed-text`** モデル（768次元 float32）を使用します。

### Ollama の導入手順
1. 公式サイト（[ollama.com](https://ollama.com)）からインストーラーをダウンロードし、インストールします。
2. 埋め込みモデルを取得します:
   ```bash
   ollama pull nomic-embed-text
   ```
3. Ollama サービスが `http://127.0.0.1:11434` で稼働していることを確認します。

### 環境変数によるカスタマイズ（必要な場合のみ）
デフォルト設定以外のモデルやエンドポイントを使用する場合は環境変数を設定してください:
- `LUCID_MEMORIES_EMBED_PROVIDER`: デフォルト `ollama`
- `LUCID_MEMORIES_EMBED_MODEL`: デフォルト `nomic-embed-text`
- `LUCID_MEMORIES_OLLAMA_URL`: デフォルト `http://127.0.0.1:11434`

※ Ollama が停止している場合でも、検索機能は自動的に SQLite FTS5 全文検索（キーワード検索）に切り替わり、システム全体が停止することはありません。

---

## 7. 動作確認と健全性テスト

セットアップ完了後、以下のコマンドでシステムの稼働状態を確認できます。

### 1. CLI ステータス確認
```bash
# ラッパーシェル経由 (POSIX)
./bin/lucid-memories status

# Python 直接実行
python src/lucid_memories/entrypoints/cli.py status
```
正常時出力例:
```json
{
  "ok": true,
  "conversation_id": "...",
  "workspace": "...",
  "jobs": [],
  "notices_unread": [],
  "reload_available": false
}
```

### 2. 全テストスイートの実行
```bash
python -m unittest discover tests
```
全 70 テストがすべて `OK` で通過することを確認します。

### 3. Web ダッシュボードの起動
ブラウザでナレッジグラフの関係性やつながりを可視化できます:
```bash
./bin/lucid-memories dashboard --open
# または
python src/lucid_memories/entrypoints/cli.py dashboard --open
```
ブラウザが起動し、`http://127.0.0.1:8765/` でグラフダッシュボード、`http://127.0.0.1:8765/guide` で仕様ガイドが表示されます。

---

## 8. トラブルシューティング

### Q1. Windows で Ladybug のインポートエラーが発生する
- **原因**: `ladybug._lbug` は内部で `libssl-3-x64.dll` を探索しますが、Windows の CPython に同梱されているファイル名は `libssl-3.dll` です。
- **対処**: 本システムは `src/lucid_memories/runtime/ladybug_runtime.py` 内で起動時に自動的に DLL を検出し、`~/.lbdb/win-openssl/libssl-3-x64.dll` へエイリアスコピーします。手動で DLL を配置する必要はありません。Python インストール先の `DLLs/` ディレクトリが存在することを確認してください。

### Q2. Cursor チャットで MCP ツールが表示されない
- **確認手順**:
  1. `~/.cursor/mcp.json` を開き、`lucid-memories` の `command` が有効な Python 実行パスを指しているか確認します。
  2. Cursor のメニューから `Developer: Toggle Developer Tools` を開き、Console タブに MCP 起動時のエラーが出ていないか確認します。
  3. コマンドパレットから `Developer: Reload Window` を実行して Cursor プロセスを再起動します。

### Q3. Hooks が実行されない・エラーで止まる
- **確認手順**:
  1. `~/.cursor/hooks.json` にイベントが定義されているか確認します。
  2. POSIX 環境の場合、`~/.cursor/hooks/lucid-memories.sh` に実行権限があるか確認します（`chmod +x ~/.cursor/hooks/lucid-memories.sh`）。
  3. 本システムのフックハンドラは予期せぬ例外時でも必ず `{}`（空 JSON）を返し、Cursor の通常動作をブロックしない安全設計になっています。

### Q4. 複数の Python 環境（pyenv, venv, uv 等）があり、意図しない Python が使われる
- **対処**: セットアップスクリプトに明示的に Python パスを渡して再実行してください:
  ```bash
  python skills/setup-lucid-memories/scripts/setup.py --python "C:\Users\<user>\AppData\Local\Programs\Python\Python314\python.exe"
  # または POSIX
  ./skills/setup-lucid-memories/scripts/setup.sh --python "/home/<user>/.pyenv/shims/python3"
  ```

---

## 9. 環境のリセットと再初期化

完全にクリーンな状態からやり直したい場合は、以下の手順を実行します。

1. **データベースの初期化**:
   ```bash
   # DB 実ファイルの削除（必要に応じてバックアップを取ってください）
   rm -rf ~/.cursor/lucid-memories/.db/
   ```
2. **Cursor 設定からの解除（必要な場合のみ）**:
   - `~/.cursor/mcp.json` から `"lucid-memories"` エントリを削除。
   - `~/.cursor/hooks.json` から `lucid-memories` を含むフックコマンドを削除。
3. **再セットアップ**:
   ```bash
   ./skills/setup-lucid-memories/scripts/setup.sh    # または .\skills\setup-lucid-memories\scripts\setup.ps1
   ```
