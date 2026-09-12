# lucid (lucid-memories)

Cursor の AI エージェント同士がセッションやワークスペースをまたいで知識・文脈・作業状態を共有・引き継ぐためのローカル永続化ストア＆ナレッジグラフシステムです。

会話履歴を丸ごとコピーするのではなく、エージェントが必要な時に必要な情報だけをトークン予算（Token Budget）内で小さく参照できるように設計されています。

---

## リポジトリ全体配置マップ

ルート直下は製品のガバナンス・決め事・全体構成マップを最前面に配置し、実装コードは `src/` 配下に集約しています。

```text
lucid-memories/
├── README.md               # 本ファイル: 製品概要、全体構成マップ、インターフェース一覧
├── SETUP_GUIDE.md          # セットアップガイド: 新規環境構築・再構築・トラブルシューティング
├── ARCHITECTURE.md         # アーキテクチャ設計書（内部詳細・数理モデル・データフロー）
├── DEVELOPER_GUIDE.md      # 開発者ガイド: 文書先行の実装プロセスの流れ・起点の判定
├── pyproject.toml          # Python パッケージメタデータ & 依存定義
├── .gitignore              # Git 除外設定（DB・キャッシュ・一時ファイル）
├── .cursorignore           # Cursor インデックス除外設定（DB・バイナリ）
│
├── docs/                   # 【製品の決め事】要求・要件・仕様・設計・決定事項（ADR）
│   ├── needs/              # 要求定義 (NEED-*)
│   ├── requirements/       # 要件定義 (REQ-*)
│   ├── specifications/     # 詳細仕様 (SPEC-*)
│   ├── design/             # アーキテクチャ・詳細設計 (DSN-*)
│   ├── decisions/          # 意思決定ログ / ADR (ADR-*)
│   ├── actors/             # アクター定義 (ACT-*)
│   └── usecases/           # ユースケース (UC-*)
│
├── persona/                # 【振る舞いの定義】常時適用のユーザー方針・永続ペルソナ（gitignore対象・セットアップ時に自動生成）
│   ├── persona.json        # ペルソナ基本設定（ユーザー固有・自動初期化）
│   └── user-rules.json     # ユーザー固有ルール（ユーザー固有・自動初期化）
│
├── skills/                 # 【Cursor エージェント用スキル正本】
│   ├── lucid-memories/     # 基本操作スキル (SKILL.md)
│   ├── relay/              # セッション間引き継ぎスキル (SKILL.md)
│   ├── save-idea/          # アイデア・メモ永続化スキル (SKILL.md)
│   └── setup-lucid-memories/ # 環境再構築スキル
│       ├── SKILL.md        # スキル定義文書
│       └── scripts/        # 再構築セットアップスクリプト群 (setup.py, setup.sh, setup.ps1)
│
├── src/                    # 【アプリケーションコード集約】
│   └── lucid_memories/     # Python パッケージ本体
│       ├── entrypoints/    # 外部接続口（CLI, Cursor Hooks, MCP Server）
│       │   ├── cli.py      # 管理用コマンドライン
│       │   ├── hook.py     # Cursor Hooks IPC ハンドラ
│       │   └── mcp_server.py # Cursor MCP サーバー (JSON-RPC 2024-11-05)
│       │
│       ├── core/           # 中核ドメインロジック
│       │   ├── api.py      # 公開ファサード API
│       │   ├── memory.py   # 記憶ライフサイクル（減衰・活性度・統合）
│       │   ├── retrieval.py# 検索・スコアリング・監査ログ
│       │   ├── graph.py    # ナレッジグラフ（Map: Directive/Procedure/Material）
│       │   ├── persona.py  # ペルソナ動的構築・自己学習
│       │   └── gate.py     # 権限・フィルタリングゲート（Auto vs Ask）
│       │
│       ├── storage/        # データ永続化層
│       │   ├── db.py       # SQLite 接続・WAL トランザクション管理
│       │   ├── blobs.py    # 不変 CAS (Content-Addressable Storage)
│       │   ├── paths.py    # パス解決ユーティリティ
│       │   ├── schema.sql  # SQLite データベーススキーマ
│       │   └── schema.cypher # Ladybug グラフスキーマ
│       │
│       ├── web/            # 可視化 UI・運用コンソール
│       │   ├── backend/      # ダッシュボード HTTP API・サービス
│       │   ├── frontend/     # 正本運用コンソール (React 19 + TypeScript + Vite + Sonner)
│       │   └── dashboard.py  # React アプリ配信用 HTTP サーバー (Port 8765)
│       │
│       └── runtime/        # 外部モデル・実行時ユーティリティ
│           ├── embedding.py      # Ollama (nomic-embed-text) ベクトル生成
│           ├── ladybug_runtime.py# Ladybug DB ネイティブ接続
│           └── util.py           # 共通ユーティリティ（トークン見積もり等）
│
├── tests/                  # 単体・統合テストスイート
├── scripts/                # 運用・メンテナンス・移行スクリプト
└── bin/                    # 実行用ラッパーシェル (bin/lucid-memories)
```

---

## 主な役割と解決する課題

1. **セッション・チャット間の知識共有**
   - 過去のチャットで得た知見（`fact`, `decision`, `finding`, `idea` など）を保存し、別のチャットセッションからキーワード検索（FTS）や意味検索（Vector Search）で引き出すことができます。
2. **コンパクション（Compact）や中断からの復帰**
   - コンテキスト長が上限に達して Cursor が会話を圧縮（compact）した際にも、スナップショット（Pack）から必要な状態を `reload` して作業を継続できます。
3. **バックグラウンドジョブと状態連携**
   - 時間のかかる作業やサブエージェントの実行状態（`job`）を管理し、別セッションから進行状況の確認や競合の検知（`lease` / `notice`）を行えます。
4. **グローバル Persona の管理・注入**
   - ワークスペース固有のルールとは別に、ユーザー共通の行動方針や応答ルール（`[global persona]`）を一元管理し、各セッションのプロンプトへ自動注入します。
5. **記憶のライフサイクルと温度管理（忘却と再活性化）**
   - 時間経過と参照頻度に応じた「温度（活性度）」や「減衰スコア（Decay）」を計算し、必要な記憶を優先しつつ使われない記憶を緩やかに退色（Fade）させます。

---

## 主要な構成要素

| 要素 | 役割 | 仕組み |
|---|---|---|
| **Knowledge Store** | 事実・知見・決定事項の保存と検索 | SQLite + FTS5、および Ollama（`nomic-embed-text`）による 768 次元ベクトル検索（`vsearch`） |
| **Map（関係グラフ）** | 指示（Directive）・処理（Procedure）・資料（Material）・文脈（Context）の意味的結合 | Ladybug（グラフDB）を用いた関連リンク（`ABOUT`, `IN_CONTEXT`, `USES`, `TRIGGERS` など） |
| **Memory Lifecycle & Temperature** | 記憶の鮮度・活性度（温度）の管理と減衰 | 参照回数と経過日数による活性度計算、半減期（Decay Half-Life）、退色（Fading） |
| **Packs & Relay** | セッション間や Compact 時の軽量引き継ぎ | 予算制約付きの構造化データ（Primer / Handoff / Snapshot） |
| **Global Persona** | 全ワークスペース共通のユーザー方針管理 | 予算管理された Markdown/テキストをプロンプト冒頭に常時注入 |

---

## 記憶のライフサイクルと温度管理

保存された記憶が溢れて検索精度が落ちるのを防ぐため、生物の記憶に近い「温度」「減衰」「再活性化」のモデルを採用しています。

### 1. 表示温度 / 活性度 (Temperature)
- **概念**: その記憶が「今どれくらい旬か」「直近どれくらい活発に使われているか」を示す指標（0%〜100%）。
- **計算要素**: 参照回数（`access_count`）と直近の参照日時（`last_accessed_at`）からの経過日数。
- **ダッシュボード連携**: Webダッシュボード（`dashboard`）のグラフ上で、高温なノードほど大きく暖色で強調表示されます。

### 2. 減衰スコアと半減期 (Exponential Decay)
- **記憶スコア**: 確信度（`confidence`）、重要度（`importance`）、注目度（`salience`）、および半減期による減衰係数の積で算出されます。
  $$\text{Score} = \text{confidence} \times \text{importance} \times (0.5 + 0.5 \times \text{salience}) \times 2^{-\frac{\Delta t}{\text{half\_life}}}$$
- **種別ごとのデフォルト半減期**:
  - `decision`（決定事項）: 365日（長期間保持）
  - `warning` / `fact`（警告・事実）: 180日
  - `idea`（アイデア）: 120日
  - `finding`（調査結果）: 60日
  - `handoff`（引き継ぎ）: 30日（短期間で減衰）

### 3. 再活性化 (Reinforcement / Touch)
- 検索や想起（`recall` / `search` / `vsearch`）で記憶が実際に参照されると、自動的に `last_accessed_at` とアクセス回数が更新されます。
- これにより記憶の温度が上がり、減衰がリセットされて再び上位に現れやすくなります。

### 4. 退色と整理 (Fading / Sweep)
- スコアが閾値を下回った記憶は勝手に削除（DELETE）されることはありません。
- `faded`（退色）ステータスに移行し、通常の検索結果ノイズにならないよう隠蔽されます。データ自体は SQLite に保持されるため、将来の再参照や昇格も可能です。
- バックグラウンドの `memory_worker` や `sweep` コマンドで定期的に整理されます。

---

## 自動化と安全設計（パイプライン方針）

エージェントの自律性と安全性を両立するため、操作ごとに明確な権限分離が行われています。

- **自動で行う操作（Auto）**:
  - 指示に応じた過去知識の想起・検索（`recall`, `search`, `vsearch`）
  - 得られた事実の記録（`remember`）
  - 関係性の下書き・提案（`link`）
- **ユーザー承認を求める操作（Ask）**:
  - 関係リンクの正式確定（`confirm`）
  - 誤った関連付けの禁止（`forbid`）
  - 知識の無効化・非表示化（`archive`）

---

## セットアップと別PCでの再構築

新しいマシンや別のPC環境に `lucid-memories` を再構築するための手順です。
自動化スクリプトにより、依存解決・Cursor MCP設定・Cursor Hooks配線・スキル配置・初期データベース初期化を一括で行うことができます。

### 1. 前提条件
- **Python 3.10+** (3.11〜3.14 推奨)
- **Git**
- **Ollama** (任意。ベクトル検索 `vsearch` を利用する場合に起動し `ollama pull nomic-embed-text`)

### 2. リポジトリの配置
```bash
# 推奨配置先 (~/.cursor/lucid-memories)
git clone <repository-url> ~/.cursor/lucid-memories
cd ~/.cursor/lucid-memories
```

### 3. ワンライナーセットアップ

お使いの OS に応じて以下のスクリプトを実行します。

- **POSIX (macOS / Linux)**:
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

※ 特定の Python インタプリタを使用する場合は `--python "<path-to-python>"` を付与できます。

#### セットアップスクリプトの処理内容
1. `pip install -e .` による依存関係（`ladybug` 等）のインストール
2. `~/.cursor/mcp.json` への `lucid-memories` MCP サーバーの安全な登録・マージ
3. `~/.cursor/hooks/lucid-memories.*` スクリプト生成および `~/.cursor/hooks.json` への全17種フックイベントの登録・マージ
4. `~/.cursor/skills/` へのバンドルスキル（`lucid-memories`, `relay`, `save-idea`, `setup-lucid-memories`）の配置
5. `persona/` ディレクトリの作成およびユーザー固有ファイル（`persona/user-rules.json`, `persona/persona.json`）の正規スキーマでの初期生成（既存ファイルは上書きせず保持）
6. データベースファイル（`.db/lucid-memories.sqlite`, `.db/map.lbdb`）の自動初期化と自己診断テスト（`cli status` & `persona validate`）

### 4. エージェントスキルによる再構築
Cursor の AI エージェントに以下のように指示するだけで、エージェント自身が環境調査から配線・動作確認まで自律的に実行することも可能です。

> 「setup-lucid-memories スキルを実行して環境を再構築して」

### 5. 反映
セットアップ完了後、Cursor を再起動（またはコマンドパレットから `Developer: Reload Window` を実行）して MCP サーバーおよび Hooks を有効化してください。

---

## インターフェース

### 1. MCP ツール (`user-lucid-memories`)
Cursor のチャット内でエージェントがツール呼び出しとして直接利用します。

| カテゴリ | ツール名 | 実行方針 | 説明・主な用途 |
|---|---|:---:|---|
| **セッション・状態管理** | `whoami` | Auto | 現在のセッションの `conversation_id` やワークスペースを解決 |
| | `status` | Auto | 稼働セッション、実行中 Job、未読 Notice、Compact 後の再読込可否を確認 |
| | `job` | Auto | ジョブの開始（start）、状態更新（update）、完了（done）、排他取得（claim） |
| | `reload` | Auto | 会話圧縮（Compact）時に保存された最新スナップショット（Pack）を復元 |
| | `relay` | Auto | 他セッションへ引き継ぐための Handoff Pack の保存・読み込み |
| **知識管理 (Knowledge)** | `search` | Auto | キーワードによる知識・Pack の全文検索（SQLite FTS5） |
| | `vsearch` | Auto | ベクトル埋め込みによる意味的類似度検索（Vector Search） |
| | `embedding_status` | Auto | 埋め込みモデル（Ollama 等）の状態やインデックス済みベクトル件数の確認 |
| | `list` | Auto | 保存済み知識の一覧取得（kind: `fact`, `decision`, `idea` 等でフィルタ可） |
| | `load` | Auto | 指定した ID やクエリの知識/Pack をトークン予算（Token Budget）内で読込 |
| | `remember` | Auto | 新しい事実・知見・決定・アイデアなどを永続化 |
| | `archive` | **Ask** | 知識を有効期限切れにして検索から除外（※ユーザー承認が必要） |
| **ナレッジグラフ (Map)** | `recall` | Auto | 指示の型に応じた手続き（Procedure）や資料（Material）をグラフから想起 |
| | `link` | Auto | ノード間の意味的関連（`ABOUT`, `IN_CONTEXT`, `USES`, `TRIGGERS` 等）を下書き提案 |
| | `confirm` | **Ask** | 提案されたグラフのエッジを正式確定（※ユーザー承認が必要） |
| | `forbid` | **Ask** | 2つのノード間の不適切な関連付けを禁止ルールとして登録（※ユーザー承認が必要） |
| | `map` | Auto | ナレッジグラフのノード数・エッジ数の集計情報を確認 |
| **ライフサイクル・評価** | `memory` | Auto | 記憶のライフサイクル状態確認、集約・整理（worker）、候補一覧、昇格（promote） |
| | `measure` | Auto | 検索ヒット率や適合率の評価（eval）、ログからのリンク提案バックフィル（improve） |
| | `artifact` | Auto | 保存された会話の成果物やリポジトリポインタの参照・読込 |

### 2. CLI

```bash
# ラッパースクリプトを使用する場合
./bin/lucid-memories <cmd>

# 直接実行する場合
python src/lucid_memories/entrypoints/cli.py <cmd>

# 状態確認
./bin/lucid-memories status

# Webダッシュボードの起動（フル機能運用コンソール）
# 初回のみ: cd src/lucid_memories/web/frontend && npm install && npm run build
./bin/lucid-memories dashboard --open

# フロントエンド単体での開発時（Vite Hot Reload）
cd src/lucid_memories/web/frontend
npm run dev
```

---

## 詳細ドキュメント

システムの設計詳細、環境構築、レイヤー構成、内部アルゴリズム、データフロー、および実装ガイドについては以下を参照してください：

- [SETUP_GUIDE.md](SETUP_GUIDE.md): セットアップガイド（新規マシン構築、スクリプト詳細、Ollama連携、診断、トラブルシューティング）
- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md): 開発者ガイド（実装プロセスの流れ、Entrypoint判定、1:N分解、検証ゲート、TDD）
- [ARCHITECTURE.md](ARCHITECTURE.md): アーキテクチャ設計書（コンポーネント詳細、Ladybug/SQLiteハイブリッド、検索監査ログ、減衰数理モデル、ストレージレイアウト）
- [docs/](docs/): 要求定義（NEED）、要件定義（REQ）、仕様書（SPEC）、詳細設計（DSN）、意思決定（ADR）
