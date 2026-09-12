import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ViewIntro } from "../components";

export interface GlossaryItem {
  id: string;
  name: string;
  badge: string;
  badgeType: "retrieval" | "memory" | "map" | "metric" | "conversation";
  summary: string;
  detail: string;
  example?: string;
  tags: string[];
}

export const GLOSSARY_ITEMS: GlossaryItem[] = [
  {
    id: "recall",
    name: "想起 (Recall / Retrieval)",
    badge: "コア概念",
    badgeType: "retrieval",
    summary: "AIが質問を受けたとき、関連する過去の記憶を「思い出す」動作",
    detail:
      "人間が「前にこれどう決めたっけ？」と思い出すのと同じです。ユーザーがプロンプトを入力した瞬間に、裏側で記憶データベースを検索してプロンプトの文脈に自動注入します。",
    example: "「DB接続のコード書いて」と言われたとき、過去に決めた「DBはPostgreSQLを使う」という記憶を想起して回答に反映させます。",
    tags: ["想起", "recall", "retrieval", "コア概念", "思い出す"],
  },
  {
    id: "retrieval-event",
    name: "想起イベント (Retrieval Event)",
    badge: "履歴・ログ",
    badgeType: "retrieval",
    summary: "プロンプト入力時に裏で走った1回の「検索処理の記録」",
    detail:
      "「どの会話で」「どんな質問のときに」「どの記憶がヒットしたか」をひとまとまりの履歴として記録したものです。ダッシュボードでは紫色の四角（■）で表されます。",
    example: "13:45の質問「ログイン機能どう作る？」に対して発生した想起処理。",
    tags: ["想起イベント", "retrieval event", "ログ", "履歴"],
  },
  {
    id: "memory",
    name: "記憶 (Memory / SQLite)",
    badge: "データ形式",
    badgeType: "memory",
    summary: "ルールや決定事項・メモが文章として保存された個々のデータ",
    detail:
      "SQLiteデータベースに保存される個々の知識のかたまりです。タイトル、本文、タグ、種別（kind）、重要度などの属性を持っています。ダッシュボードでは橙色の丸（●）で表されます。",
    example: "タイトル「認証にはJWTを採用」、本文「OAuth2.0準拠のアクセストークンを使用すること」",
    tags: ["記憶", "memory", "sqlite", "知識", "保存"],
  },
  {
    id: "map",
    name: "Map (知識マップ / ナレッジグラフ)",
    badge: "データ形式",
    badgeType: "map",
    summary: "知識と知識の「つながり（関係性）」を網の目状に表したネットワーク",
    detail:
      "単なる文章のメモではなく、「Aという手順を実行するとBがトリガーされる」「CはDに依存している」といった構造化された関係性を管理します。ダッシュボードでは緑色の丸（●）で表されます。",
    example: "「デプロイ手順」--[TRIGGERS]-->「キャッシュパージ」という前後関係。",
    tags: ["map", "知識マップ", "ナレッジグラフ", "関係性", "グラフ"],
  },
  {
    id: "temperature",
    name: "表示温度 / 温度 (Temperature)",
    badge: "指標・ステータス",
    badgeType: "metric",
    summary: "その記憶が「今どれくらいホットに使われているか（活性度）」",
    detail:
      "最近使われたり、何度も繰り返し参照される記憶ほど温度が高くなります（ダッシュボードで円が大きく、暖色で表示されます）。長期間使われない記憶は徐々に冷めていき、忘却・重要度低下の目安になります。",
    example: "表示温度 90%: 今日頻繁に参照されている旬な記憶。\n表示温度 15%: しばらく使われていない冷えつつある記憶。",
    tags: ["温度", "表示温度", "temperature", "活性度", "減衰"],
  },
  {
    id: "memory-score",
    name: "memory_score (記憶スコア)",
    badge: "指標・ステータス",
    badgeType: "metric",
    summary: "記憶の「定着度・総合重要度」を表すスコア",
    detail:
      "想起された累計回数や利用の直近性（鮮度）をもとに自動算出されます。高いほど、エージェントにとって「頼りにされている重要な知識」であることを示します。",
    tags: ["memory_score", "記憶スコア", "スコア", "定着度", "重要度"],
  },
  {
    id: "score-rank",
    name: "score & 結果順位 (Rank)",
    badge: "検索結果",
    badgeType: "metric",
    summary: "その質問に対して「どれくらい合致したか（類似度）」と「何番目か」",
    detail:
      "`score` は質問文と記憶のベクトル類似度やキーワード一致度（0.0〜1.0）。`結果順位` はその想起イベントにおいて何番目に優先度高くヒットしたかを示します。",
    tags: ["score", "rank", "結果順位", "類似度", "順位"],
  },
  {
    id: "conversation",
    name: "会話 (Conversation / Session)",
    badge: "単位",
    badgeType: "conversation",
    summary: "Cursorでの個別のチャットスレッド（やり取りの単位）",
    detail:
      "エージェントとやり取りしている1つのチャット窓です。各会話内で質問するたびに想起イベントが発生します。ダッシュボードでは水色の丸（●）で表されます。",
    tags: ["会話", "conversation", "session", "セッション", "チャット"],
  },
  {
    id: "kind",
    name: "Kind (種別)",
    badge: "分類",
    badgeType: "memory",
    summary: "記憶の内容がどのような性質のものかの分類ラベル",
    detail:
      "代表的な分類: decision（決定事項）、rule（規約・ルール）、fact（システム前提事実）、workflow / procedure（作業手順）、gotcha（落とし穴・注意点）、idea（今後のメモ）。",
    tags: ["kind", "種別", "decision", "rule", "fact", "workflow", "gotcha", "idea"],
  },
  {
    id: "scope",
    name: "Scope (スコープ)",
    badge: "適用範囲",
    badgeType: "metric",
    summary: "その記憶が有効なプロジェクトの範囲",
    detail:
      "workspace: 開いているリポジトリ（フォルダ）限定の記憶。\nuser: どのプロジェクトを開いていても適用されるグローバルな共通記憶（言語の好みなど）。",
    tags: ["scope", "スコープ", "workspace", "user", "適用範囲"],
  },
  {
    id: "relay",
    name: "Relay (リレー / ハンドオフ)",
    badge: "引き継ぎ",
    badgeType: "retrieval",
    summary: "会話が長くなったとき、別の新チャットに文脈を手渡す「引き継ぎパック」",
    detail:
      "現在の会話の要点、直近の作業フォーカス、使用中のスキルなどを1つの「Pack」としてまとめ、新しいチャットで再開（reload）できるようにする機能です。",
    tags: ["relay", "リレー", "ハンドオフ", "引き継ぎ", "pack", "reload"],
  },
  {
    id: "global-persona",
    name: "Global Persona (グローバルペルソナ)",
    badge: "常時適用",
    badgeType: "conversation",
    summary: "全セッションのプロンプトに常時自動注入されるユーザー固有の方針",
    detail:
      "「常に日本語で回答する」「まず結論から述べる」といった、Workspaceに関わらず常に守ってほしいユーザー共通の行動規範を各ターンへ自動で注入します。",
    tags: ["global persona", "ペルソナ", "行動規範", "常時適用", "方針"],
  },
];

export interface FaqItem {
  id: string;
  question: string;
  answer: string;
}

export const FAQ_ITEMS: FaqItem[] = [
  {
    id: "faq-1",
    question: "用語がたくさんあって覚えきれません。まず何を把握すればいいですか？",
    answer:
      "まずは次の3つだけ把握すれば十分です！\n1. 記憶: 保存されている知識（ルールや決定メモ）。\n2. 想起: 質問されたときに過去の記憶を「思い出す」こと。\n3. 表示温度: 最近・よく使われている知識ほど熱い（＝円が大きい）。",
  },
  {
    id: "faq-2",
    question: "記憶（SQLite）と Map はどう使い分けるのですか？",
    answer:
      "記憶（SQLite）は「文章としてのルールやメモ」（例: 「PR作成時はテストを必須とする」）。\nMap は「手順やトピック同士の前後関係やつながり」（例: 「ビルドが成功したらテストを実行する」）。\n日常的な決定事項は自動的に「記憶」として保存され、複雑な因果関係やワークフローがある場合に「Map」が活用されます。",
  },
  {
    id: "faq-3",
    question: "想起グラフに何もノードが表示されません。",
    answer:
      "会話セレクトが「すべて」になっているか、期間（開始・終了日時）が過去すぎる設定になっていないか確認してください。\nまだ会話でプロンプト処理や記憶の想起が行われていない場合はログが存在しないため、チャットでやり取りを進めると自然とノードが増えていきます。",
  },
  {
    id: "faq-4",
    question: "記憶の「温度」が下がると消えてしまうのですか？",
    answer:
      "勝手に消えることはありません。温度が下がっても記憶は SQLite に残り続けます。\n温度は「最近活発に使われているか」を示す指標であり、検索スコアの並び順の参考として使われます。再び関連する質問があれば想起され、温度も再び上がります。",
  },
];

export const FLOW_STEPS = [
  {
    step: 1,
    title: "ユーザーが質問・指示",
    desc: "Cursorチャットでプロンプトを入力します。（例: 「認証処理を追加して」）",
  },
  {
    step: 2,
    title: "自動想起 (Retrieval)",
    desc: "フックが質問内容を解析し、関連する過去の決定（例: JWT採用）をSQLite/Mapから瞬時に検索。",
  },
  {
    step: 3,
    title: "回答生成に反映",
    desc: "AIは過去の決定を踏まえた前提で回答を返します。ユーザーが同じ説明を繰り返す必要がありません。",
  },
  {
    step: 4,
    title: "活性度更新 & 新規記憶",
    desc: "使われた記憶は「温度」が上昇。新しい設計決定があれば remember で自動・手動保存されます。",
  },
];

export function filterGlossary(items: GlossaryItem[], query: string): GlossaryItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter((item) => {
    return (
      item.name.toLowerCase().includes(q) ||
      item.summary.toLowerCase().includes(q) ||
      item.detail.toLowerCase().includes(q) ||
      item.badge.toLowerCase().includes(q) ||
      item.tags.some((tag) => tag.toLowerCase().includes(q))
    );
  });
}

export function GuidePage() {
  const [searchTerm, setSearchTerm] = useState("");
  const [openFaqs, setOpenFaqs] = useState<Record<string, boolean>>({ "faq-1": true });

  const filteredItems = useMemo(
    () => filterGlossary(GLOSSARY_ITEMS, searchTerm),
    [searchTerm],
  );

  const toggleFaq = (id: string) => {
    setOpenFaqs((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
  };

  const copyTerm = (name: string, summary: string) => {
    const text = `${name}\n${summary}`;
    navigator.clipboard?.writeText(text).then(
      () => {
        toast.success("用語の説明をコピーしました", { description: name });
      },
      () => {
        toast.info(name, { description: summary });
      },
    );
  };

  const handleClearSearch = () => {
    setSearchTerm("");
    toast.info("検索をリセットしました");
  };

  return (
    <div className="view guide-view">
      <ViewIntro
        description="Cursorエージェントの記憶共有・想起システムを分かりやすく解説"
        kicker="DOCUMENTATION & GLOSSARY"
        title="lucid-memories 用語・機能ガイド"
      >
        <div className="guide-header-actions">
          <Link className="primary-button" to="/recall-graph">
            想起グラフ画面を開く →
          </Link>
        </div>
      </ViewIntro>

      <nav aria-label="Table of contents" className="guide-toc-nav">
        <div className="guide-toc-title">目次</div>
        <ul className="guide-toc-list">
          <li><a href="#about">1. lucid-memories とは？</a></li>
          <li><a href="#glossary">2. 用語辞典</a></li>
          <li><a href="#screen">3. 想起グラフ画面の歩き方</a></li>
          <li><a href="#flow">4. 会話と記憶が動く仕組み</a></li>
          <li><a href="#faq">5. よくある質問 (FAQ)</a></li>
        </ul>
      </nav>

      {/* SECTION 1 */}
      <section className="guide-section" id="about">
        <h2 className="guide-section-heading">
          <span className="section-icon">💡</span> 1. lucid-memories とは？
        </h2>
        <p className="guide-lead">
          <strong>lucid-memories</strong> は、複数のチャットやセッションをまたいで
          <strong>「AIエージェントに過去の決定・ルール・知見を記憶させ、必要なときに自動で思い出させる」</strong>
          ための共有記憶システムです。
        </p>

        <div className="guide-card why-card">
          <h3 className="why-title">なぜ必要なの？</h3>
          <p className="why-body">
            通常のAIチャットは、チャットが新しくなったりコンテキストが長くなると過去のやり取りや決定事項を忘れてしまいます。<br />
            lucid-memories を使うことで、「以前決めた設計方針」「このプロジェクト特有のルール」「ハマりどころの教訓」などを永続化し、
            関連するプロンプトが入力された瞬間に<strong>自動で想起（検索）してAIに思い出させる</strong>ことができます。
          </p>
        </div>
      </section>

      {/* SECTION 2 */}
      <section className="guide-section" id="glossary">
        <h2 className="guide-section-heading">
          <span className="section-icon">📚</span> 2. 用語辞典
        </h2>
        <p className="guide-lead">
          画面やコマンドで使われている専門用語はそのまま維持しつつ、日常の言葉や直感的な例えで噛み砕いて解説します。
        </p>

        <div className="guide-search-bar">
          <div className="search-input-wrapper">
            <input
              aria-label="用語を検索"
              className="guide-search-input"
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="用語を検索… (例: 想起, 温度, Map, score, kind)"
              type="search"
              value={searchTerm}
            />
          </div>
          {searchTerm && (
            <button
              className="secondary-button"
              onClick={handleClearSearch}
              type="button"
            >
              クリア
            </button>
          )}
          <span className="guide-search-count">
            {filteredItems.length} 件の用語
          </span>
        </div>

        {filteredItems.length === 0 ? (
          <div className="guide-empty-state">
            <p>「{searchTerm}」に一致する用語は見つかりませんでした。</p>
            <button className="primary-button" onClick={handleClearSearch} type="button">
              検索フィルターをクリア
            </button>
          </div>
        ) : (
          <div className="guide-card-grid">
            {filteredItems.map((item) => (
              <article className="guide-card term-card" key={item.id}>
                <header className="term-card-header">
                  <h3 className="term-name">{item.name}</h3>
                  <span className={`term-badge badge-${item.badgeType}`}>
                    {item.badge}
                  </span>
                </header>
                <div className="term-summary">{item.summary}</div>
                <p className="term-detail">{item.detail}</p>
                {item.example && (
                  <div className="term-example">
                    <span className="example-label">例:</span> {item.example}
                  </div>
                )}
                <footer className="term-card-footer">
                  <div className="term-tags">
                    {item.tags.slice(0, 3).map((t) => (
                      <span className="term-tag" key={t}>
                        #{t}
                      </span>
                    ))}
                  </div>
                  <button
                    className="term-copy-btn"
                    onClick={() => copyTerm(item.name, item.summary)}
                    title="用語と概要をコピー"
                    type="button"
                  >
                    コピー
                  </button>
                </footer>
              </article>
            ))}
          </div>
        )}
      </section>

      {/* SECTION 3 */}
      <section className="guide-section" id="screen">
        <h2 className="guide-section-heading">
          <span className="section-icon">🖥️</span> 3. 想起グラフ画面の歩き方
        </h2>
        <p className="guide-lead">
          ダッシュボード（想起グラフ）画面は、<strong>「左から右へ情報が流れる 3 カラム構成」</strong>になっています。
        </p>

        <div className="table-card">
          <table className="guide-table">
            <thead>
              <tr>
                <th style={{ width: "25%" }}>カラム / 要素</th>
                <th style={{ width: "25%" }}>視覚表現</th>
                <th>何を意味しているか・見どころ</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <strong>① 会話 (Conversation)</strong>
                  <br />
                  <span className="subtle-text">左カラム</span>
                </td>
                <td>
                  <span className="vis-legend conversation-legend">● 水色の丸</span>
                </td>
                <td>
                  Cursorでのチャットセッション。どの会話の中で記憶が使われたかを一覧できます。クリックするとその会話の詳細や、絞り込みができます。
                </td>
              </tr>
              <tr>
                <td>
                  <strong>② 想起イベント (Retrieval)</strong>
                  <br />
                  <span className="subtle-text">中央カラム</span>
                </td>
                <td>
                  <span className="vis-legend retrieval-legend">■ 紫色の四角</span>
                </td>
                <td>
                  質問送信時に走った検索処理。クリックすると、「そのときユーザーが入力したプロンプト」や「検索リクエストID」を確認できます。
                </td>
              </tr>
              <tr>
                <td>
                  <strong>③ 記憶 / Map (Item)</strong>
                  <br />
                  <span className="subtle-text">右カラム</span>
                </td>
                <td>
                  <span className="vis-legend memory-legend">● 橙色: SQLite記憶</span>
                  <br />
                  <span className="vis-legend map-legend">● 緑色: Map知識</span>
                </td>
                <td>
                  実際にAIに参照された知識。クリックすると、<strong>保存されている記憶の本文全文</strong>や、何回使われたか、重要度スコアが右パネルに表示されます。
                </td>
              </tr>
              <tr>
                <td>
                  <strong>円の大きさ</strong>
                </td>
                <td>大小の丸</td>
                <td>
                  <strong>表示温度（活性度）</strong>を表します。直近・頻繁に使われている記憶ほど大きく表示されます。
                </td>
              </tr>
              <tr>
                <td>
                  <strong>実線 (recalled)</strong>
                </td>
                <td>紫色・灰色の実線</td>
                <td>
                  想起イベントによって<strong>実際に検索ヒットして呼び出された</strong>つながりです。
                </td>
              </tr>
              <tr>
                <td>
                  <strong>破線 (map-relation)</strong>
                </td>
                <td>緑色の点線</td>
                <td>
                  知識マップ（Map）の中で、知識同士が「関連」「トリガー」としてリンクしているつながりです。
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className="guide-card toolbar-guide-card">
          <h3 className="toolbar-guide-title">ツールバーの使い方</h3>
          <ul className="toolbar-guide-list">
            <li>
              <strong>会話セレクト:</strong> 特定のチャットセッションだけに絞り込んで、その会話で呼び出された記憶だけを見ることができます。
            </li>
            <li>
              <strong>データソース:</strong> 「すべて」「記憶（SQLiteのみ）」「Mapのみ」を切り替えて、見たい知識の種類に集中できます。
            </li>
            <li>
              <strong>開始・終了日時:</strong> 「今日の作業分だけ見たい」「過去3日間の推移を見たい」といった期間指定が可能です。
            </li>
          </ul>
        </div>
      </section>

      {/* SECTION 4 */}
      <section className="guide-section" id="flow">
        <h2 className="guide-section-heading">
          <span className="section-icon">🔄</span> 4. 会話と記憶が動く仕組み
        </h2>
        <p className="guide-lead">
          日常のコーディング作業の中で、lucid-memories がどのように自動で動いているかの流れです。
        </p>

        <div className="guide-flow-diagram">
          {FLOW_STEPS.map((step) => (
            <div className="guide-flow-step" key={step.step}>
              <div className="flow-step-num">{step.step}</div>
              <h3 className="flow-step-title">{step.title}</h3>
              <p className="flow-step-desc">{step.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* SECTION 5 */}
      <section className="guide-section" id="faq">
        <h2 className="guide-section-heading">
          <span className="section-icon">❓</span> 5. よくある質問 (FAQ)
        </h2>

        <div className="guide-faq-list">
          {FAQ_ITEMS.map((faq) => {
            const isOpen = Boolean(openFaqs[faq.id]);
            return (
              <div className="guide-faq-item" key={faq.id}>
                <button
                  aria-expanded={isOpen}
                  className="guide-faq-q"
                  onClick={() => toggleFaq(faq.id)}
                  type="button"
                >
                  <span className="faq-q-text">{faq.question}</span>
                  <span className="faq-arrow">{isOpen ? "▲" : "▼"}</span>
                </button>
                {isOpen && (
                  <div className="guide-faq-a">
                    {faq.answer.split("\n").map((line, idx) => (
                      <p key={idx}>{line}</p>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <footer className="guide-page-footer">
        <p>lucid-memories — Cursor agent shared memory and recall visualization</p>
        <Link className="guide-footer-link" to="/recall-graph">
          想起グラフダッシュボードを開く →
        </Link>
      </footer>
    </div>
  );
}
