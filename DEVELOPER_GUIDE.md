# 開発者ガイド (Developer Guide)

本書は、**lucid-memories** の機能追加・修正・リファクタリングを行う開発者（人間の開発者および AI エージェント）向けの実装ガイドラインです。

当プロジェクトでは、実装コードを直接書き始める「コードファースト」を避け、指示の抽象度に応じた文書（要求または要件）を起点として精緻化・検証を経てから実装に着手する**文書駆動開発（Doc-First / Spec-Driven）**を徹底しています。

---

## 1. 実装プロセスの全体フロー

実装作業は必ず以下のステップに沿って進行します。

```text
[実装指示・開発タスク]
        │
        ▼
【ステップ 1: 起点の判定 (Entrypoint Decision)】
  ・動機・課題・大まかな要望 ──> パスA: 要求起点 (docs/needs/)
  ・具体的成果・受入条件・契約 ──> パスB: 要件起点 (docs/requirements/)
        │
        ▼
【ステップ 2: 文書の起票と分解 (Authoring & Refinement)】
  ・パスA: NEED-xxxx.md (1ファイル) を起票し、REQ-yyyy.md へ 1:N に分解
  ・パスB: REQ-xxxx.md を直接起票
  ・必要に応じて 仕様 (SPEC-*) や 設計 (DSN-*) を作成
        │
        ▼
【ステップ 3: 検証ゲート (Validation Gate)】
  ・python scripts/validate_docs.py を実行し、スキーマ適合 (エラー0件) を確認
        │
        ▼
【ステップ 4: 実装とテスト (Implementation & Testing)】
  ・定義された受入条件 (AC) に基づくテストケースの作成
  ・src/ 配下のプロダクションコード実装
  ・テスト実行と品質検証
```

---

## 2. 起点の判定 (Entrypoint Decision)

ユーザーやイシューからの実装指示を受け取った際、その**抽象度**と**情報の性質**に応じて起点（Entrypoint）となるドキュメント種別を決定します。

| 起点 | 指示の特徴 | 記録先ファイル | 依存関係 (`depends_on`) |
|---|---|---|---|
| **要求起点** (`need`) | 背景、動機、課題、大まかな要望（例:「〜したい」「〜で困っている」「こういう機能がほしい」）、または複数の独立した成果に分解される抽象度の高い指示 | `docs/needs/NEED-xxxx.md` | `depends_on: []`<br>※後続の `REQ-` 側から参照される |
| **要件起点** (`requirement`) | 観測可能な成果、具体的な振る舞い、受け入れ条件（AC）、API契約の変更など、単一の成果として閉じた具体的な指示 | `docs/requirements/REQ-xxxx.md` | `depends_on: []` |

---

## 3. 文書の起票と精緻化ルール

### パスA: 要求起点 (`need`) の場合

1. **要求の起票 (`NEED-`)**:
   - `docs/needs/` に最新の連番で `NEED-xxxx.md` を作成します（1ファイル1要求）。
   - 見出し構成は `### Background`、`### Problem`、`### Desired Outcome` の順序を守ります。
   - `depends_on: []` とします。

   ```markdown
   ---
   schema_version: 3
   id: NEED-0001
   kind: need
   title: ユーザーが記憶を自然言語で直感的に検索したい
   status: draft
   created: "2026-09-12"
   updated: "2026-09-12"
   scope: local
   depends_on: []
   tags: []
   links: []
   ---
   ## Content

   ### Background
   ユーザーは日々メモや事実を保存するが、後から探す際に正確なキーワードを思い出せないことがある。

   ### Problem
   完全一致検索だけでは、表現の揺らぎやあいまいな記憶に対応できない。

   ### Desired Outcome
   自然言語の文脈や類似度から、関連する記憶を直感的に発見できること。
   ```

2. **要件への分解 (1:N 構成)**:
   - 1つの要求に対して、それを満たす観測可能な成果ごとに**1つ以上の独立した要件（`REQ-yyyy.md`）に分解**します。
   - 各要件文書のフロントマターで `depends_on: ["NEED-xxxx"]` を指定し、親となる要求IDを1つだけ記述します（**1要件が複数要求に依存することは禁止**）。

   ```markdown
   ---
   schema_version: 3
   id: REQ-0020
   kind: requirement
   title: 利用者が自然言語クエリで類似する記憶を検索できる
   status: draft
   created: "2026-09-12"
   updated: "2026-09-12"
   scope: local
   depends_on: ["NEED-0001"]
   tags: []
   links: []
   ---
   ## Content

   ### Statement
   利用者は、完全一致するキーワードだけでなく、自然言語クエリによるベクトル類似度検索で記憶を検索できる。

   ### Acceptance Criteria
   - AC-001: Given 保存済みの記憶群がある When 利用者が自然言語でクエリを送信する Then コサイン類似度の高い順に記憶が返却される
   ```

3. **仕様・設計への展開 (任意/必要に応じて)**:
   - 振る舞いや契約を具体化する場合は `docs/specifications/SPEC-zzzz.md`（`depends_on: ["REQ-yyyy"]`）を作成。
   - 内部構造・アーキテクチャの決定は `docs/design/DSN-aaaa.md`（`depends_on: ["SPEC-zzzz"]`）を作成。

---

### パスB: 要件起点 (`requirement`) の場合

1. **要件の起票 (`REQ-`)**:
   - `docs/requirements/REQ-xxxx.md` を作成します。
   - 親となる要求が存在しない直接起票のため、`depends_on: []` とします。
   - 1成果につき1ファイルとし、受け入れ条件（AC）を `Given ... When ... Then ...` の形式で定義します。
2. **仕様・設計への展開**:
   - パスAと同様に、必要に応じて `SPEC-` および `DSN-` を作成します。

---

## 4. 検証ゲート (Validation Gate)

コードの実装に着手する前に、作成・更新した文書がプロジェクト全体のスキーマ規約に合致しているかを必ず機械的に検証します。

```bash
python scripts/validate_docs.py
```

### ゲート合格基準
- 終了コードが `0` であり、`PASS: All XX docs files strictly follow docs-document-schema.mdc!` が出力されること。
- エラーが 1 件でもある場合は、コード実装に進まず文書を修正してください。

---

## 5. コード実装とテスト

文書と検証ゲートが通過した後、コードの実装を行います。

1. **テスト先行 (TDD) の推奨**:
   - 要件の `Acceptance Criteria` や仕様の `Contract` に基づき、`tests/` 配下にテストコードを作成します。
2. **実装の配置**:
   - エントリポイント: `src/lucid_memories/entrypoints/`
   - コアロジック: `src/lucid_memories/core/`
   - ストレージ層: `src/lucid_memories/storage/`
   - Web/可視化: `src/lucid_memories/web/`
3. **テストの実行**:
   ```bash
   python -m unittest discover tests
   ```

---

## 6. 実装完了のチェックリスト

コミットや作業完了の前に、以下の項目を満たしていることを確認してください。

- [ ] 指示の抽象度に応じた起点文書（`NEED-` または `REQ-`）が作成されている。
- [ ] 要求起点の場合、1つの `NEED-` に対して要件 `REQ-` が 1:N で正しく紐付いている（`REQ-` の `depends_on` に親 `NEED-` を指定）。
- [ ] `python scripts/validate_docs.py` がエラー 0 件でパスしている。
- [ ] 文書で定義された受け入れ条件（AC）を検証するテストが存在し、すべてパスしている。
- [ ] 既存の機能やテストに対するリグレッションが発生していない。
