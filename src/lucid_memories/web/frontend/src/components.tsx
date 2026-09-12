import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { formatDate, formatNumber, shortId, statusClass } from "./format";
import { useRefresh, type ResourceState } from "./hooks";

function OverviewIcon() {
  return (
    <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
      <rect height="7" rx="1.5" width="7" x="3" y="3" />
      <rect height="7" rx="1.5" width="7" x="14" y="3" />
      <rect height="7" rx="1.5" width="7" x="14" y="14" />
      <rect height="7" rx="1.5" width="7" x="3" y="14" />
    </svg>
  );
}

function SessionsIcon() {
  return (
    <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function JobsIcon() {
  return (
    <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
      <rect height="14" rx="2" width="20" x="2" y="7" />
      <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
    </svg>
  );
}

function MemoryIcon() {
  return (
    <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </svg>
  );
}

function RecallGraphIcon() {
  return (
    <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
      <circle cx="18" cy="5" r="3" />
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="19" r="3" />
      <line x1="8.59" x2="15.42" y1="13.51" y2="17.49" />
      <line x1="15.41" x2="8.59" y1="6.51" y2="10.49" />
    </svg>
  );
}

function ActivityIcon() {
  return (
    <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  );
}

function DailyIcon() {
  return (
    <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
      <rect height="18" rx="2" width="18" x="3" y="4" />
      <line x1="16" x2="16" y1="2" y2="6" />
      <line x1="8" x2="8" y1="2" y2="6" />
      <line x1="3" x2="21" y1="10" y2="10" />
    </svg>
  );
}

export function GuideIcon() {
  return (
    <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
      <line x1="8" x2="16" y1="7" y2="7" />
      <line x1="8" x2="14" y1="11" y2="11" />
    </svg>
  );
}

export function GlossaryIcon() {
  return (
    <svg fill="none" height="18" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24" width="18">
      <path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z" />
      <line x1="9" x2="15" y1="21" y2="21" />
    </svg>
  );
}

export interface GlossaryTerm {
  term: string;
  en: string;
  badge: string;
  badgeColor?: "indigo" | "mint" | "coral" | "yellow";
  description: string;
}

export const GLOSSARY_TERMS: GlossaryTerm[] = [
  {
    term: "想起",
    en: "Recall / Retrieval",
    badge: "コア動作",
    badgeColor: "indigo",
    description: "AIエージェントが指示を受け取ったとき、関連する過去の決定やルールをデータベースから「思い出す」動作です。",
  },
  {
    term: "想起イベント",
    en: "Retrieval Event",
    badge: "履歴単位",
    badgeColor: "indigo",
    description: "ユーザーがプロンプトを入力した1回のタイミングで裏で走った検索処理の記録（どの質問に対して何がヒットしたか）。",
  },
  {
    term: "記憶",
    en: "Memory / SQLite",
    badge: "データ",
    badgeColor: "coral",
    description: "設計方針、コーディング規約、メモなど、文章としてSQLiteに永続化された具体的な知識・ファクトです。",
  },
  {
    term: "Map",
    en: "Knowledge Graph",
    badge: "グラフ構造",
    badgeColor: "mint",
    description: "「AのあとにBを行う」「CはDに依存する」といった知識同士の関係性・手順を管理する構造化ネットワークです。",
  },
  {
    term: "表示温度",
    en: "Temperature",
    badge: "活性度",
    badgeColor: "yellow",
    description: "記憶の「熱さ」。最近使われたり頻繁に参照されるほど温度が高く（暖色に）なります。使われないと時間経過で冷えます。",
  },
  {
    term: "定着度",
    en: "memory_score",
    badge: "重要度スコア",
    badgeColor: "yellow",
    description: "記憶の総合重要度スコア。想起された累計回数や鮮度をもとに自動算出され、重要な記憶ほど高くなります。",
  },
  {
    term: "動的ペルソナ",
    en: "Dynamic Persona",
    badge: "文脈注入",
    badgeColor: "indigo",
    description: "会話の文脈とトークン予算に応じて、プロンプト送信前に自動注入されるルール・制約・指示です。",
  },
  {
    term: "セッション会話",
    en: "Session / Conversation",
    badge: "チャット単位",
    badgeColor: "mint",
    description: "Cursor IDE 内の個別のチャットセッション。過去セッションの知識が想起によって現在セッションに還元されます。",
  },
];

export function QuickGlossaryModal({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) {
  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    },
    [onClose],
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [isOpen, handleKeyDown]);

  if (!isOpen) return null;

  return (
    <div
      aria-labelledby="glossary-modal-title"
      aria-modal="true"
      className="modal-backdrop"
      onClick={onClose}
      role="dialog"
    >
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="modal-kicker">LUCID-MEMORIES OBSERVABILITY</div>
            <h2 id="glossary-modal-title">💡 主要用語の早見表</h2>
          </div>
          <button
            aria-label="閉じる"
            className="modal-close-button"
            onClick={onClose}
            type="button"
          >
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p className="modal-lead">
            lucid-memories の想起グラフや運用コンソールで使用される主要な用語と概念のクイック解説です。
          </p>
          <div className="glossary-grid">
            {GLOSSARY_TERMS.map((item) => (
              <div className="glossary-card" key={item.term}>
                <div className="glossary-card-header">
                  <div>
                    <span className="glossary-term">{item.term}</span>
                    <span className="glossary-en">{item.en}</span>
                  </div>
                  <span
                    className={`status-pill ${
                      item.badgeColor ? `status-pill-${item.badgeColor}` : ""
                    }`}
                  >
                    {item.badge}
                  </span>
                </div>
                <p className="glossary-desc">{item.description}</p>
              </div>
            ))}
          </div>
        </div>
        <div className="modal-footer">
          <span className="modal-footer-note">
            より詳しい仕組みやFAQは完全ガイドへ
          </span>
          <div className="modal-footer-actions">
            <Link
              className="guide-button primary"
              onClick={onClose}
              to="/guide"
            >
              📖 完全ガイドを開く (/guide)
            </Link>
            <button className="secondary-button" onClick={onClose} type="button">
              閉じる
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const navigation = [
  { to: "/", label: "Overview", icon: <OverviewIcon />, end: true },
  { to: "/sessions", label: "Sessions", icon: <SessionsIcon /> },
  { to: "/jobs", label: "Jobs", icon: <JobsIcon /> },
  { to: "/memory", label: "Memory", icon: <MemoryIcon /> },
  { to: "/recall-graph", label: "Recall graph", icon: <RecallGraphIcon /> },
  { to: "/activity", label: "Activity index", icon: <ActivityIcon /> },
  { to: "/daily", label: "Daily summary", icon: <DailyIcon /> },
  { to: "/guide", label: "Guide", icon: <GuideIcon /> },
];

const titles: Record<string, string> = {
  "/": "Overview",
  "/sessions": "Session history",
  "/jobs": "Job history",
  "/memory": "Memory operations",
  "/recall-graph": "Conversation recall graph",
  "/activity": "Activity index",
  "/daily": "Daily summary",
  "/guide": "Documentation & Feature Guide",
};

export function AppShell() {
  const location = useLocation();
  const { autoRefresh, setAutoRefresh, refresh } = useRefresh();
  const [isGlossaryOpen, setIsGlossaryOpen] = useState(false);

  const title = location.pathname.startsWith("/sessions/")
    ? "Session detail"
    : location.pathname.startsWith("/artifacts/")
      ? "Artifact detail"
      : titles[location.pathname] || "lucid-memories Dashboard";

  const handleRefreshClick = () => {
    refresh();
    toast.info("最新データに更新中...");
  };

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      ) {
        return;
      }
      if (e.key === "?") {
        e.preventDefault();
        setIsGlossaryOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">L</div>
          <div>
            <div className="brand-name">lucid-memories</div>
            <div className="brand-caption">OPERATIONS CONSOLE</div>
          </div>
        </div>
        <nav aria-label="Main navigation" className="nav">
          {navigation.map((item) => (
            <NavLink
              className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
              end={item.end}
              key={item.to}
              to={item.to}
            >
              {item.icon}
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-utility">
          <div className="sidebar-section-divider" />
          <button
            className="nav-utility-item"
            onClick={() => setIsGlossaryOpen(true)}
            title="主要用語の早見表を表示 (? キー)"
            type="button"
          >
            <GlossaryIcon />
            <span>用語早見表</span>
            <span className="kbd-shortcut">?</span>
          </button>
          <NavLink
            className={({ isActive }) => `nav-utility-item${isActive ? " active" : ""}`}
            title="用語・機能ガイド画面を開く (/guide)"
            to="/guide"
          >
            <GuideIcon />
            <span>完全ガイド</span>
          </NavLink>
        </div>
        <div className="sidebar-foot">
          <div className="status-dot" />
          <div>
            <div className="muted-label">READ-ONLY DATA SOURCE</div>
            <div className="source-label">lucid-memories SQLite</div>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <div className="eyebrow">LUCID-MEMORIES / OBSERVABILITY</div>
            <h1>{title}</h1>
          </div>
          <div className="topbar-actions">
            <button
              className="glossary-button"
              onClick={() => setIsGlossaryOpen(true)}
              title="主要用語の早見表を表示 (?)"
              type="button"
            >
              <GlossaryIcon />
              <span>用語早見表</span>
            </button>
            <Link
              className="guide-button"
              title="用語・機能ガイドを開く (/guide)"
              to="/guide"
            >
              <GuideIcon />
              <span>ガイド</span>
            </Link>
            <label className="refresh-toggle">
              <input
                checked={autoRefresh}
                onChange={(event) => setAutoRefresh(event.target.checked)}
                type="checkbox"
              />
              Auto refresh
            </label>
            <span className="read-only">
              <span className="read-dot" /> READ ONLY
            </span>
            <button className="refresh-button" onClick={handleRefreshClick} type="button">
              <svg fill="none" height="14" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" width="14">
                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
              </svg>
              Refresh
            </button>
          </div>
        </header>
        <Outlet />
        <footer className="footer">
          <div className="footer-left">
            <span>lucid-memories Dashboard · local read-only console</span>
            <span className="footer-sep">·</span>
            <button
              className="footer-link-btn"
              onClick={() => setIsGlossaryOpen(true)}
              type="button"
            >
              💡 用語早見表
            </button>
            <span className="footer-sep">·</span>
            <Link
              className="footer-link"
              to="/guide"
            >
              📖 完全ガイド (/guide)
            </Link>
          </div>
          <span>{autoRefresh ? "Updates every 30 seconds" : "Manual refresh"}</span>
        </footer>
      </main>

      <QuickGlossaryModal
        isOpen={isGlossaryOpen}
        onClose={() => setIsGlossaryOpen(false)}
      />
    </div>
  );
}

export function ViewIntro({
  kicker,
  title,
  description,
  children,
}: {
  kicker: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="view-intro">
      <div>
        <div className="panel-kicker">{kicker}</div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {children}
    </div>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label: string;
}) {
  return (
    <label className="search-box">
      <span aria-hidden="true">⌕</span>
      <span className="sr-only">{label}</span>
      <input
        aria-label={label}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        type="search"
        value={value}
      />
    </label>
  );
}

export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={`panel ${className}`}>{children}</section>;
}

export function PanelHeading({
  kicker,
  title,
  action,
}: {
  kicker: string;
  title: string;
  action?: ReactNode;
}) {
  return (
    <div className="panel-heading">
      <div>
        <div className="panel-kicker">{kicker}</div>
        <h3>{title}</h3>
      </div>
      {action}
    </div>
  );
}

export function StatusBadge({ value }: { value: string | null | undefined }) {
  return <span className={`status-pill ${statusClass(value)}`}>{value || "unknown"}</span>;
}

export function MetricCard({
  label,
  value,
  note,
  tone = "indigo",
}: {
  label: string;
  value: string | number;
  note: string;
  tone?: "indigo" | "mint" | "coral" | "yellow";
}) {
  return (
    <div className={`metric metric-${tone}`}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{typeof value === "number" ? formatNumber(value) : value}</div>
      <div className="metric-trend">
        <strong>●</strong> {note}
      </div>
    </div>
  );
}

export function LoadingState({ label = "読み込み中…" }: { label?: string }) {
  return (
    <div className="state-message loading-state" role="status">
      <span className="spinner" />
      {label}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return <div className="state-message">{message}</div>;
}

export function ErrorState({ error, retry }: { error: Error; retry: () => void }) {
  return (
    <div className="error-state" role="alert">
      <strong>データを読み込めませんでした。</strong>
      <span>{error.message}</span>
      <button className="secondary-button" onClick={retry} type="button">再試行</button>
    </div>
  );
}

export function ResourceState<T>({
  resource,
  children,
}: {
  resource: ResourceState<T>;
  children: (data: T) => ReactNode;
}) {
  if (resource.loading && !resource.data) return <LoadingState />;
  if (resource.error && !resource.data) return <ErrorState error={resource.error} retry={resource.retry} />;
  if (!resource.data) return <EmptyState message="データはありません。" />;
  return (
    <>
      {resource.loading && <div className="refreshing" role="status">更新中…</div>}
      {children(resource.data)}
      {resource.error && <div className="inline-warning">更新に失敗しました。前回のデータを表示しています。</div>}
    </>
  );
}

export function Pagination({
  page,
  onChange,
}: {
  page: { page: number; pages: number; total: number; has_previous: boolean; has_next: boolean };
  onChange: (page: number) => void;
}) {
  if (page.pages <= 1) return <div className="result-count">{formatNumber(page.total)} 件</div>;
  return (
    <div aria-label="Pagination" className="pagination">
      <button disabled={!page.has_previous} onClick={() => onChange(page.page - 1)} type="button">← 前へ</button>
      <span>{page.page} / {page.pages} · {formatNumber(page.total)} 件</span>
      <button disabled={!page.has_next} onClick={() => onChange(page.page + 1)} type="button">次へ →</button>
    </div>
  );
}

export function SessionLink({
  id,
  title,
  fallback,
}: {
  id?: string | null;
  title?: string | null;
  fallback?: string | null;
}) {
  if (!id) return <span>—</span>;
  return (
    <NavLink className="link" to={`/sessions/${encodeURIComponent(id)}`}>
      {title || fallback || shortId(id)}
    </NavLink>
  );
}

export function DateCell({ value }: { value?: string | null }) {
  return <span className="mono">{formatDate(value, true)}</span>;
}

export function ExpandableText({
  value,
  label = "全文",
}: {
  value?: string | null;
  label?: string;
}) {
  if (!value) return <span>—</span>;

  const copy = () => {
    const fallbackCopy = () => {
      try {
        const textArea = document.createElement("textarea");
        textArea.value = value;
        textArea.style.position = "fixed";
        textArea.style.left = "-9999px";
        textArea.style.top = "-9999px";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        document.execCommand("copy");
        document.body.removeChild(textArea);
        toast.success("クリップボードにコピーしました", {
          description: `${value.slice(0, 40)}…`,
        });
      } catch (err) {
        toast.error("コピーに失敗しました", {
          description: String(err),
        });
      }
    };

    if (navigator?.clipboard?.writeText) {
      navigator.clipboard
        .writeText(value)
        .then(() => {
          toast.success("クリップボードにコピーしました", {
            description: `${value.slice(0, 40)}…`,
          });
        })
        .catch(() => {
          fallbackCopy();
        });
    } else {
      fallbackCopy();
    }
  };

  return (
    <div className="expandable-text">
      <span className="truncate" title={value}>{value.length > 140 ? `${value.slice(0, 140)}…` : value}</span>
      <div className="text-actions">
        {value.length > 140 && <details><summary>{label}</summary><pre>{value}</pre></details>}
        <button className="copy-button" onClick={copy} title="クリップボードにコピー" type="button">
          Copy
        </button>
      </div>
    </div>
  );
}
