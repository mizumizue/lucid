# lucid-memories アーキテクチャ設計書

本書は、Cursor の AI エージェント間でセッションやワークスペースをまたいだ知識・文脈・状態の共有を実現する **lucid-memories** のアーキテクチャおよび技術詳細仕様をまとめた文書です。

---

## 1. 設計思想と基本方針

1. **会話ログ全複製の否定**
   - チャット履歴全文を共有するのではなく、構造化された「知識（Knowledge）」と「関連性（Map）」、および要約された「スナップショット（Pack）」だけを永続化します。
2. **トークン予算（Token Budget）の厳格な遵守**
   - エージェントのコンテキストウィンドウを圧迫しないよう、知識の読み出しやスナップショット復元には常にトークン予算の上限（デフォルト 2,000 トークン）を設けて動的にトリミングします。
3. **Auto / Ask の権限分離（安全設計）**
   - 知識の想起（`recall`）・検索（`search`, `vsearch`）・事実記録（`remember`）・関連付けの提案（`link proposed`）はエージェントが自動実行（Auto）します。
   - リンクの正式確定（`confirm`）・不適切な関連付けの禁止（`forbid`）・知識の無効化（`archive`）など不可逆または影響の大きい操作は必ずユーザーの承認（Ask）を必要とします。
4. **外部依存の極小化とローカル完結**
   - すべてのデータはローカル（SQLite / Ladybug / ローカルファイル）に保存されます。埋め込みもローカルの Ollama を既定とし、外部クラウドサービスへの無断データ送信を行いません。

---

## 2. システム全体構成

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                                 Cursor IDE                                  │
│                                                                             │
│   ┌─────────────────────┐                 ┌─────────────────────────────┐   │
│   │    Cursor Hooks     │                 │        Agent Session        │   │
│   │  (IPC / JSON-stdio) │                 │     (Tool Calls / LLM)      │   │
│   └──────────┬──────────┘                 └──────────────┬──────────────┘   │
└──────────────┼───────────────────────────────────────────┼──────────────────┘
               │                                           │
               ▼                                           ▼
┌───────────────────────────────┐           ┌─────────────────────────────────┐
│     entrypoints/hook.py       │           │   entrypoints/mcp_server.py     │
│   - before_submit_prompt      │           │   - JSON-RPC 2024-11-05 (stdio) │
│     (Persona + Digest 注入)   │           │   - 20 MCP ツール群             │
│   - pre_compact (Pack 保存)   │           └────────────────┬────────────────┘
│   - session / usage / events  │                            │
└──────────────┬────────────────┘                            │
               │                                             │
┌──────────────┼───────────────────────────┬─────────────────┘
│              ▼                           ▼
│      ┌────────────────────────────────────────────────┐
│      │               core/api.py (Facade)             │
│      └──────┬──────────────┬──────────────┬───────────┘
│             │              │              │
│   ┌─────────▼────────┐ ┌───▼───────────┐ ┌▼────────────────┐
│   │  core/memory.py  │ │core/persona.py│ │core/retrieval.py│
│   │ - 減衰スコア算出 │ │- 予算制御     │ │- FTS5 全文検索  │
│   │ - 温度/活性度    │ │- 差分改定     │ │- 監査ログ追跡   │
│   │ - 統合 Worker    │ │- 自動注入文   │ └────────┬─────────┘
│   └──────────────────┘ └───────────────┘          │
│                                                   │
│   ┌───────────────────────────────────────────────▼─────────────────┐
│   │                    core/graph.py (Map)                          │
│   │  - Directive / Procedure / Material / Topic / Context ノード    │
│   │  - TRIGGERS / USES / ABOUT / IN_CONTEXT / RELATED エッジ        │
│   └──────────┬────────────────────────────────────────────┬─────────┘
│              │ (Edge store)                               │ (Node index)
│              ▼                                            ▼
│   ┌───────────────────────────┐           ┌─────────────────────────┐
│   │   runtime/ladybug_runtime │           │  storage/db.py (SQLite) │
│   │   - Ladybug DB (map.lbdb) │           │  - ontology_nodes + FTS │
│   │   - Cypher エッジ・探索   │           │  - ノードメタデータ正本 │
│   └───────────────────────────┘           └──────────────┬──────────┘
│                                                          │
│   ┌───────────────────────────┐                          │
│   │ runtime/embedding.py      │                          │
│   │ - Ollama API 連携         │                          │
│   │ - nomic-embed-text 768d   │                          │
│   │ - SQLite BLOB 格納        │                          │
│   └──────────┬────────────────┘                          │
│              │                                           │
└──────────────┼───────────────────────────────────────────┼──────────────────┐
               ▼                                           ▼                  │
┌─────────────────────────────────────────────────────────────────────────┐   │
│                          ローカルストレージ層                           │   │
│  - SQLite (`~/.cursor/lucid-memories/.db/lucid-memories.sqlite`)        │   │
│  - Ladybug DB (`~/.cursor/lucid-memories/.db/map.lbdb`)                 │   │
│  - CAS Blobs (`.db/` 内 SQLite `blobs` テーブル)                        │   │
│  - Persona (`~/.cursor/lucid-memories/persona/`)                        │   │
└─────────────────────────────────────────────────────────────────────────┘   │
                                                                              │
┌───────────────────────────────┐           ┌─────────────────────────────┐   │
│      entrypoints/cli.py       │           │      web/dashboard.py       │   │
│  - 管理用コマンドライン       │           │  - 可視化 UI (Port 8765)    │   │
│  - 状態確認・手動操作         │           │  - /guide 用語集・活性度    │   │
└───────────────────────────────┘           └─────────────────────────────┘   │
                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. レイヤー構成と責務

リポジトリのソースコードは `src/lucid_memories/` 配下でクリーンにレイヤリングされています。

| レイヤー | ディレクトリ | 主なモジュール | 責務 |
|---|---|---|---|
| **Entrypoints** | `entrypoints/` | `mcp_server.py`, `cli.py`, `hook.py` | 外部境界のアダプタ。Cursor Hooks IPC、MCP stdio プロトコル、CLI 入力、HTTP を受ける。 |
| **Core** | `core/` | `api.py`, `memory.py`, `graph.py`, `retrieval.py`, `persona.py`, `gate.py` | ドメインロジック、ユースケース、オーケストレーション、トランザクション境界、権限制御。 |
| **Storage** | `storage/` | `db.py`, `schema.sql`, `blobs.py`, `paths.py` | SQLite 接続管理（WAL / FTS5）、マイグレーション、ファイルパス解決、CAS (Blob)。 |
| **Runtime** | `runtime/` | `embedding.py`, `ladybug_runtime.py`, `util.py` | 外部サービス（Ollama）や C/ネイティブ拡張（Ladybug）のアダプタ、ユーティリティ。 |
| **Web** | `web/` | `dashboard.py`, `backend/`, `frontend/` | React 運用コンソールの配信、HTTP API、関係グラフと用語・仕様ガイド。 |

---

## 4. コアサブシステム仕様

### 4.1 Knowledge Store (SQLite & Content-Addressable Storage)

知識の本体（`knowledge`）および大きなテキスト/バイナリ（`blobs`）を管理します。

- **メタデータ管理 (`knowledge` テーブル)**:
  - `id`: 一意な識別子（UUID またはプレフィックス付き ID）
  - `kind`: `decision`, `warning`, `fact`, `finding`, `idea`, `handoff`, `pointer`
  - `scope`: `global`, `workspace`, `session`
  - `workspace_root`: 対象ワークスペースの正規化パス（スコープがワークスペースの場合）
  - `title`, `body`: 本文がインライン制限（既定 2,000 文字）以内の場合は SQLite に直接保持
  - `blob_sha`: 本文が大きい場合は CAS (`blobs`) に退避し SHA-256 ハッシュを保持
  - `memory_status`: `active`, `faded`, `archived`
- **CAS (`blobs` テーブル)**:
  - SHA-256 を主キーとする不変ストレージ。重複排除と大きな出力・資料の効率的保存を実現。SQLite データベース内に内包。
- **WAL モードと整合性**:
  - SQLite は `PRAGMA journal_mode = WAL;` および `PRAGMA foreign_keys = ON;` で動作し、MCP サーバ、Hook、CLI からの同時読み取りと安全な書き込みを担保。

### 4.2 Map / ナレッジグラフ (Ladybug DB & SQLite Ontology)

エージェントの作業コンテキスト（指示・処理・資料・話題・文脈）を構造化グラフとして保持します。本文は SQLite に置き、グラフにはポインタと関係性のみを保持します。

- **ノード種別**:
  - `Directive`: ユーザーの指示やゴール（正規化タイトルで識別）
  - `Procedure`: 実行可能な処理やスキル、コマンド、スクリプト（ポインタ情報を持つ）
  - `Material`: 処理に必要な資料、ファイル、ドキュメント、Knowledge ID
  - `Topic`: 扱っている話題・ドメイン
  - `Context`: 実行環境や前提文脈
- **エッジ（リレーション）**:
  - `TRIGGERS` (`Directive` → `Procedure`): 指示がどの処理を起動するか
  - `USES` (`Procedure` → `Material`): 処理がどの資料を参照するか
  - `ABOUT` (`*` → `Topic`): 各ノードがどの話題に関するものか
  - `IN_CONTEXT` (`*` → `Context`): 各ノードがどの文脈下にあるか
  - `RELATED` (`*` → `*`): 汎用的な関連（`sense`, `label` を付与）
- **ハイブリッドグラフ基盤**（ADR-0011）:
  - ノード正本: SQLite `ontology_nodes` + FTS（解決・メタデータ）
  - エッジ正本: `Ladybug DB` (`map.lbdb`) による Cypher クエリ（`link`, `recall`, `confirm`）
  - Ladybug は必須依存。エッジの SQLite フォールバックはない

### 4.3 ハイブリッド検索パイプライン (Retrieval Pipeline)

キーワード検索とベクトル検索を組み合わせたハイブリッド検索を提供します。

```text
[クエリ] ──┬──> [FTS5 Full-Text Search] (SQLite トライグラム / トークナイザ) ──┐
          │                                                                  ├──> [ランク統合 & 重み付け] ──> [Hits]
          └──> [Vector Search (vsearch)] ──> [Ollama: nomic-embed-text 768d] ┘
```

1. **全文検索 (`search`)**:
   - SQLite FTS5 仮想テーブルを利用（トークナイザの環境に応じた自動フォールバック）。
   - クエリの形態素・単語単位での一致を高速走査。
2. **意味ベクトル検索 (`vsearch`)**:
   - 埋め込みプロバイダ: Ollama（既定モデル: `nomic-embed-text`、768 次元）。
   - テーブル: `embeddings`（`model`, `dimensions`, `vector` [リトルエンディアン float32 BLOB]）。
   - クエリベクトルをインメモリで生成し、SQLite 内の全件 BLOB とコサイン類似度を一括計算。
3. **想起 (`recall`) のフィルタ条件**:
   - `recall` は知識ダンプではなく、現在の指示の型に応じた手続きと資料を絞り込む。
   - 採用条件: 正式確認済み（`confirmed`）、または「実行回数 $\ge 3$ かつ 最終実行日が 14 日以内（Hot 判定）」。
   - 拒否済み・禁止（`denied`, `FORBIDS`）のエッジは完全除外。
4. **検索監査ログ (`retrieval_requests` / `retrieval_results` / `retrieval_logs`)**:
   - 誰が、どのセッションで、どのプロンプトを起点に、何を検索し、どの結果が返されたかを完全記録。
   - ログから「検索結果の欠け（gaps）」を分析し、精度改善のためのリンク提案（`measure --improve`）に活用。

### 4.4 記憶のライフサイクルと温度管理 (Memory Lifecycle & Temperature)

記憶の鮮度・活性度を数理モデルで制御し、情報の陳腐化とノイズ化を防ぎます。

1. **減衰スコア (Decay Score)**:
   $$\text{Score} = \text{confidence} \times \text{importance} \times (0.5 + 0.5 \times \text{salience}) \times 2^{-\frac{\Delta t}{\text{half\_life}}}$$
   - $\Delta t$: 直近参照日時（`last_accessed_at`。未参照時は作成日時）からの経過日数
   - 半減期（`half_life`）の種別規定値:
     - `decision`: 365 日
     - `warning` / `fact`: 180 日
     - `idea`: 120 日
     - `finding`: 60 日
     - `handoff`: 30 日
2. **表示温度 / 活性度 (Temperature)**:
   $$\text{Temperature} = \min\left(1.0, \frac{\text{access\_count}}{10}\right) \times 2^{-\frac{\text{age\_days}}{14}}$$
   - ダッシュボード上で直近・頻繁に使われている記憶を 0%〜100% で可視化。
3. **再活性化 (`touch`)**:
   - `search` / `vsearch` / `recall` で選択・参照された記憶は即座に `access_count` を加算し `last_accessed_at` を現在時刻に更新（温度上昇・減衰リセット）。
4. **自動退色スイープ (`sweep`)**:
   - スコアが閾値（`MIN_VISIBLE_SCORE = 0.025`）を下回った記憶は `faded` ステータスに変更。
   - 物理削除はせず SQLite に保持し、検索結果のノイズからのみ隔離。
5. **バックグラウンド統合 (`memory_tasks` → `memory_candidates`)**:
   - 会話ログから有用な決定や知見を非同期タスクとしてエンキューし、ワーカーが `memory_candidates`（候補）を抽出。承認を経て Knowledge へ昇格（`promote`）。

### 4.5 コンテキスト制御とハンドオフ (Packs & Relay)

トークン上限対策およびセッション間引き継ぎのための構造化データ管理です。

- **Pack 種別**:
  - `primer`: セッション初期化時に必要な最小知識セット
  - `handoff`: 別チャットやエージェントへ作業を引き継ぐためのパッケージ
  - `compact_snapshot`: 会話圧縮（Compact）直前のコンテキストスナップショット
  - `job_board`: バックグラウンドジョブの一覧・状況
- **Pre-Compact と Reload パイプライン**:
  1. Cursor のコンテキスト枯渇時に `handle_pre_compact` フックが発火。
  2. 現時点の重要状態・タスク・参照資料を `compact_snapshot` Pack として SQLite に保存。
  3. 新しい圧縮セッションでエージェントが `status` を呼ぶと `reload_available=true` を検知。
  4. `reload` ツールでトークン予算（2,000 トークン）内に要約されたスナップショットを復元。

### 4.6 セッション・ジョブ・排他制御 (Sessions, Jobs & Leases)

- **セッションバインディング (`session_bindings`)**:
  - Cursor Hook プロセスと常駐 MCP Server プロセスは別環境で動くため、`mcp-current` キーで最新のセッション ID とワークスペースを共有。
- **ジョブ追跡 (`jobs`, `job_events`)**:
  - サブエージェントの起動・停止、シェル実行などの状態（`pending`, `running`, `blocked`, `done`, `error`）を追跡。
- **リソース排他制御 (`leases`)**:
  - 複数エージェントが同一リソース（ファイルやタスク）に競合アクセスするのを防ぐ TTL 付きリースロック（デフォルト 5 分）。

### 4.7 グローバル Persona 管理 (Global Persona)

- **正本ディレクトリ**: `~/.cursor/lucid-memories/persona/`
  - `persona.json`: ユーザーの基本方針・言語・トーン設定
  - `user-rules.json`: ルール一覧
- **プロンプト自動注入**:
  - `handle_before_submit_prompt` フックが、プロンプト送信前にトークン予算（既定 8,000 トークン）内で Persona テキストを構築し、システムプロンプト先頭に `[global persona]` として自動挿入。
- **自己学習サイクル**:
  - 会話中のユーザー指示から繰り返し現れる指示傾向を `persona_candidates` に抽出し、改定履歴（`persona_revisions`）を記録。

---

## 5. 主要データフロー

### 5.1 プロンプト送信・実行時フロー

```text
User Input
   │
   ▼
[Cursor IDE] ──(before_submit_prompt IPC)──> [hook.py]
                                                 │
                                                 ├── 1. Persona 読み込み・予算内整形
                                                 ├── 2. 直近プロンプトから簡易 search/recall 実行
                                                 ├── 3. [global persona] + Digest 結合テキスト返却
                                                 │
   ┌─────────────────────────────────────────────┘
   ▼
[Agent 実行開始 (プロンプト注入済み)]
   │
   ├── (思考・作業中)
   │     ├─ 必要に応じて MCP: recall / search / vsearch
   │     ├─ 得られた事実を MCP: remember (Auto)
   │     └─ 関連性を MCP: link (proposed, Auto)
   │
   ├── (重要変更・確定時)
   │     └─ ユーザーに確認し、MCP: confirm / forbid / archive (Ask)
   │
   ▼
[Cursor IDE] ──(post_tool_use / session_end IPC)──> [hook.py]
                                                        │
                                                        ├── usage_events / conversation_events 記録
                                                        └── memory_tasks に統合ワーカータスクをエンキュー
```

---

## 6. ストレージレイアウトと環境変数

### 6.1 ディレクトリ構成

デフォルトルート: `~/.cursor/lucid-memories/`

```text
~/.cursor/lucid-memories/
├── .db/
│   ├── lucid-memories.sqlite      # メイン SQLite データベース（知識・CAS Blobs・ログ）
│   ├── lucid-memories.sqlite-wal  # WAL ログファイル
│   └── map.lbdb/                  # Ladybug グラフデータベースディレクトリ
└── persona/                       # グローバル Persona 正本ファイル
    ├── persona.json
    └── user-rules.json
```

### 6.2 主な環境変数

| 環境変数名 | デフォルト値 | 説明 |
|---|---|---|
| `LUCID_MEMORIES_HOME` | `~/.cursor/lucid-memories` | データ格納ベースディレクトリ |
| `LUCID_MEMORIES_EMBED_PROVIDER` | `ollama` | 埋め込みプロバイダ |
| `LUCID_MEMORIES_EMBED_MODEL` | `nomic-embed-text` | ベクトル埋め込みモデル名 |
| `LUCID_MEMORIES_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama API のエンドポイント URL |
| `LUCID_MEMORIES_CONVERSATION_ID` | (なし) | セッション ID の明示的オーバーライド |
| `LUCID_MEMORIES_DASHBOARD_PORT` | `8765` | Web ダッシュボードの待ち受けポート |
