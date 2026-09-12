---
name: setup-lucid-memories
description: >-
  rebuild and configure the lucid-memories environment (Python dependencies, MCP
  server, Cursor hooks, skills, and database) on a machine. Use when setting up
  lucid-memories on a new machine, restoring the environment after migration,
  or repairing broken Cursor integrations.
---

# setup-lucid-memories

別マシンまたは初期化された環境において、**lucid-memories** システム一式（依存ライブラリ・MCP サーバー・Cursor Hooks・共通スキル・永続化ストア）を決定的に再構築する。

自動化の正本: `skills/setup-lucid-memories/scripts/setup.py`（または同ディレクトリの `setup.sh` / `setup.ps1`）。手動設定を避け、スクリプト駆動で安全に配線する。

## 先導語

**probe** — 既存の環境（OS、Python 実行パス、既存の `mcp.json` / `hooks.json`）を非破壊で把握する。
**wire** — MCP・Hooks・Skills を Cursor 環境へマージし、既存設定を保護しながら結合する。
**verify** — データベース初期化と CLI 応答をテストし、動作可能状態を確定する。

## 手順

### 1. probe（環境調査）

実行環境の前提を確認する:

1. **Python**: 3.10 以上のインタプリタを特定する（`python3 --version` または `python --version`）。
2. **配置パス**: リポジトリが `~/.cursor/lucid-memories` にあるか確認する。別パスにある場合はシンボリックリンクまたは `--python` オプションの準備をする。
3. **Cursor 設定**: `~/.cursor/mcp.json` と `~/.cursor/hooks.json` の存在を確認する。既存のキーを保持してマージする方針をとる。
4. **Ollama**: `http://127.0.0.1:11434/api/tags` への到達性を確認し、`nomic-embed-text` の有無を控える。

**完了基準**: Python 3.10+ の実行バイナリパスが特定され、リポジトリの配置と既存設定の有無が把握されている。

### 2. wire（依存解決と Cursor 配線）

決定的なセットアップランナーを実行する:

- POSIX (macOS / Linux):
  ```bash
  ./skills/setup-lucid-memories/scripts/setup.sh
  ```
- Windows (PowerShell):
  ```powershell
  .\skills\setup-lucid-memories\scripts\setup.ps1
  ```
- クロスプラットフォーム直接実行:
  ```bash
  python skills/setup-lucid-memories/scripts/setup.py
  ```

※ Python パスを明示指定する場合は `python skills/setup-lucid-memories/scripts/setup.py --python "<path-to-python>"`。

この実行により以下が一括で処理される:
- `pip install -e .` による依存関係（`ladybug` 等）の解決
- `~/.cursor/mcp.json` への `lucid-memories` MCP サーバーの安全なマージ
- `~/.cursor/hooks/lucid-memories.*` ラッパー生成および `~/.cursor/hooks.json` への 17 フックイベントのマージ
- `~/.cursor/skills/` へのバンドルスキル（`lucid-memories`, `relay`, `save-idea`, `setup-lucid-memories`）の配置
- `persona/`（gitignore対象）の作成とユーザー固有ファイル（`persona/user-rules.json`, `persona/persona.json`）の安全な初期生成（既存ファイルは保持）

**完了基準**: `skills/setup-lucid-memories/scripts/setup.py` が終了コード 0 で完了し、`mcp.json` に `lucid-memories`、`hooks.json` にフック定義、`~/.cursor/skills/` に各スキル、`persona/` に正規スキーマのファイルが存在する。

### 3. verify（健全性検証）

再構築されたシステムが正常に応答するか検証する:

1. **CLI ステータス & ペルソナ検証**:
   ```bash
   python src/lucid_memories/entrypoints/cli.py status
   python src/lucid_memories/entrypoints/cli.py persona validate
   ```
   返却される JSON の `ok: true` を確認する。初回実行により `.db/lucid-memories.sqlite` および `.db/map.lbdb` が自動生成・マイグレーションされる。
2. **テスト実行**:
   ```bash
   python -m unittest discover tests
   ```
   テストスイートを実行し、主要機能の健全性を確認する。

**完了基準**: `cli status` および `persona validate` が `ok: true` を返し、`.db/` 配下にデータベースが生成され、テストが通過している。

### 4. guide（反映とユーザー案内）

設定を Cursor プロセスに反映させるための案内を行う:

1. **Cursor の再読み込み**: MCP サーバーおよび Hooks の更新を反映するため、Cursor を再起動（または `Developer: Reload Window`）するようユーザーに伝える。
2. **Ollama 状態の案内**:
   - 稼働中かつモデルあり: ベクトル検索（`vsearch`）が即時有効である旨を伝える。
   - 未稼働またはモデルなし: SQLite 全文検索（FTS5）で動作中であり、ベクトル検索を有効にするには `ollama pull nomic-embed-text` が必要である旨を伝える。

**完了基準**: 再起動の必要性と Ollama の稼働状況（ベクトル検索有効または FTS フォールバック動作）がユーザーに報告されている。

## リファレンス

- **設定ファイル正本**:
  - MCP: `~/.cursor/mcp.json`
  - Hooks: `~/.cursor/hooks.json`
  - Hook 実行スクリプト: `~/.cursor/hooks/lucid-memories.cmd` (Win) / `~/.cursor/hooks/lucid-memories.sh` (POSIX)
  - 記憶ストア: `~/.cursor/lucid-memories/.db/`
  - ペルソナ正本: `~/.cursor/lucid-memories/persona/`
- **Windows OpenSSL 留意事項**:
  - `ladybug._lbug` は `libssl-3-x64.dll` を要求する。`src/lucid_memories/runtime/ladybug_runtime.py` が Python 同梱の `libssl-3.dll` から自動補完するため手動配置は不要。
- **既存設定の保護**:
  - `setup.py` は既存の `mcp.json` や `hooks.json` に別ツール（他 MCP や他 Hook）が定義されていても、対象キーのみを上書き/追記し、他の設定を破壊しない。
