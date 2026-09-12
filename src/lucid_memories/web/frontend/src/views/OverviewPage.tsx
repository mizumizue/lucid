import { Link } from "react-router-dom";
import { api } from "../api";
import {
  DateCell,
  EmptyState,
  MetricCard,
  Panel,
  PanelHeading,
  ResourceState,
  StatusBadge,
  ViewIntro,
} from "../components";
import { formatCost, formatDate, formatNumber, sessionDisplayTitle, shortId } from "../format";
import { useRefresh, useResource } from "../hooks";
import type { DailyPoint } from "../types";

export function OverviewPage() {
  const { revision } = useRefresh();
  const overview = useResource((signal) => api.overview(signal), [revision]);
  const daily = useResource((signal) => api.daily(14, signal), [revision]);

  return (
    <div className="view">
      <ViewIntro
        description="セッション、ジョブ、コンパクション、検索ログを lucid-memories SQLite から読み取ります。"
        kicker="REAL-TIME MEMORY BUS"
        title="共有コンテキストの流れをひとつの画面で。"
      />
      <ResourceState resource={overview}>
        {(data) => (
          <>
            <div className="metric-grid">
              <MetricCard label="Live sessions" note={`${formatNumber(data.counts.sessions)} total`} value={data.counts.live_sessions} />
              <MetricCard label="Open jobs" note={`${formatNumber(data.counts.jobs)} total`} tone="mint" value={data.counts.open_jobs} />
              <MetricCard
                label="Input tokens"
                note={data.usage.status === "available" ? `${formatNumber(data.usage.events)} usage events` : data.usage.status}
                tone="coral"
                value={data.usage.status === "available" ? formatNumber(data.usage.input_tokens) : "—"}
              />
              <MetricCard
                label="Cost (USD)"
                note={data.usage.status === "available" ? `${formatNumber(data.usage.output_tokens)} output tokens` : data.usage.status}
                tone="yellow"
                value={data.usage.status === "available" ? formatCost(data.usage.cost_usd) : "—"}
              />
            </div>
            <div className="content-grid">
              <Panel className="wide-panel">
                <PanelHeading kicker="ACTIVITY" title="日次アクティビティ" action={<span className="panel-note">直近 14 日</span>} />
                <ActivityChart days={daily.data?.data || []} />
              </Panel>
              <Panel>
                <PanelHeading kicker="DISTRIBUTION" title="利用モデル" />
                <div className="model-list">
                  {data.models.length ? data.models.slice(0, 6).map((model, index) => {
                    const total = data.models.reduce((sum, item) => sum + item.sessions, 0) || 1;
                    return (
                      <div className="model-row" key={model.model}>
                        <div className="model-top"><span>{model.model}</span><span className="model-count">{formatNumber(model.sessions)} sessions</span></div>
                        <div className="model-track"><div className={`model-fill fill-${index % 3}`} style={{ width: `${model.sessions / total * 100}%` }} /></div>
                      </div>
                    );
                  }) : <EmptyState message="モデル情報はまだありません。" />}
                </div>
              </Panel>
            </div>
            <div className="content-grid lower-grid">
              <Panel className="wide-panel">
                <PanelHeading kicker="RECENT" title="最近のセッション" action={<Link className="text-link" to="/sessions">すべて見る →</Link>} />
                <div className="session-list">
                  {data.recent_sessions.length ? data.recent_sessions.map((session) => (
                    <Link className="session-row" key={session.conversation_id} to={`/sessions/${encodeURIComponent(session.conversation_id)}`}>
                      <div><strong>{sessionDisplayTitle(session.title, session.last_prompt, session.conversation_id)}</strong><small>{shortId(session.conversation_id)}</small></div>
                      <span className="session-model">{session.model || "未記録"}</span>
                      <StatusBadge value={session.status} />
                      <DateCell value={session.last_heartbeat_at || session.updated_at} />
                    </Link>
                  )) : <EmptyState message="セッションはまだありません。" />}
                </div>
              </Panel>
              <Panel>
                <PanelHeading kicker="DATA COVERAGE" title="計測状況" />
                <div className={`coverage-card coverage-${data.usage.status}`}>
                  <div className="coverage-icon">{data.usage.status === "available" ? "◉" : "◒"}</div>
                  <h4>{coverageTitle(data.usage.status)}</h4>
                  <p>{data.usage.message}</p>
                  <div className="coverage-meter"><span style={{ width: data.usage.status === "available" ? "100%" : "40%" }} /></div>
                  <small>{data.index.events} events · {data.index.artifacts} artifacts</small>
                </div>
              </Panel>
            </div>
            <div className="content-grid lower-grid">
              <Panel>
                <PanelHeading kicker="HOOK COVERAGE" title="収集しているイベント" />
                {data.hooks ? (
                  <div className={`coverage-card coverage-${data.hooks.status}`}>
                    <h4>{hookTitle(data.hooks.status)}</h4>
                    <p>{data.hooks.message}</p>
                    <small>
                      {formatNumber(data.hooks.events)} events
                      {data.hooks.missing.length ? ` · missing ${data.hooks.missing.slice(0, 3).join(", ")}` : ""}
                    </small>
                  </div>
                ) : <EmptyState message="hook 収集状況は未対応です。" />}
              </Panel>
              <Panel>
                <PanelHeading kicker="PERSONA" title="Global persona" />
                {data.persona ? (
                  <div className="status-summary">
                    <div className="status-summary-row"><span>Scope</span><strong>{data.persona.scope || "未設定"}</strong></div>
                    <StatusSummaryRow label="Sections" value={data.persona.sections} />
                    <StatusSummaryRow label="Token estimate" value={data.persona.token_estimate ?? undefined} suffix={data.persona.token_budget ? `/ ${formatNumber(data.persona.token_budget)}` : ""} />
                    <StatusSummaryRow label="Pending candidates" value={data.persona.candidates.counts.pending} />
                    <p className="muted">{data.persona.message}</p>
                  </div>
                ) : <EmptyState message="persona は未対応です。" />}
              </Panel>
            </div>
            <div className="content-grid lower-grid">
              <Panel>
                <PanelHeading
                  kicker="MEMORY LIFECYCLE"
                  title="記憶の状態"
                  action={<Link className="text-link" to="/memory">詳細 →</Link>}
                />
                {data.memory ? (
                  <div className="status-summary">
                    <StatusSummaryRow label="Active memories" value={data.memory.knowledge.counts.active} />
                    <StatusSummaryRow label="Faded memories" value={data.memory.knowledge.counts.faded} />
                    <StatusSummaryRow label="Pending tasks" value={data.memory.tasks.counts.pending} />
                    <StatusSummaryRow label="Pending candidates" value={data.memory.candidates.counts.pending} />
                  </div>
                ) : <EmptyState message="Memory lifecycle は未対応です。" />}
              </Panel>
              <Panel>
                <PanelHeading kicker="INDEX HEALTH" title="検索・埋め込み・Map" />
                <div className="status-summary">
                  <StatusSummaryRow label="Embeddings" value={data.embeddings?.total} suffix={data.embeddings?.available ? "vectors" : "未対応"} />
                  <StatusSummaryRow label="Map nodes" value={data.map?.nodes} suffix={data.map?.graph_available ? "graph ready" : "SQLite index"} />
                    <StatusSummaryRow label="Proposed Map edges" value={proposedEdgeCount(data.map?.relation_counts)} />
                  <StatusSummaryRow label="Retrieval hit rate" value={data.retrieval?.hit_rate == null ? undefined : Math.round(data.retrieval.hit_rate * 100)} suffix={data.retrieval?.hit_rate == null ? "—" : "%"} />
                  <StatusSummaryRow label="Stored blobs" value={data.storage?.blobs.count} suffix={data.storage?.blobs.available ? "blobs" : "未対応"} />
                  <StatusSummaryRow label="Pack tokens" value={data.context?.token_estimate} suffix={data.context?.available ? "est." : "未対応"} />
                </div>
              </Panel>
            </div>
          </>
        )}
      </ResourceState>
      {daily.error && <div className="inline-warning">日次アクティビティを読み込めませんでした。</div>}
    </div>
  );
}

function StatusSummaryRow({
  label,
  value,
  suffix,
}: {
  label: string;
  value?: number;
  suffix?: string;
}) {
  return (
    <div className="status-summary-row">
      <span>{label}</span>
      <strong>{value == null ? "—" : formatNumber(value)} {suffix || ""}</strong>
    </div>
  );
}

function proposedEdgeCount(relationCounts?: Record<string, number>): number | undefined {
  if (!relationCounts) return undefined;
  return Object.entries(relationCounts)
    .filter(([key]) => key.endsWith("_proposed"))
    .reduce((total, [, count]) => total + count, 0);
}

function hookTitle(status: string) {
  return {
    available: "Hook を収集中",
    partial: "一部の hook のみ収集",
    no_events: "Hook 収集を待機中",
    unavailable: "Hook は未対応",
  }[status] || "Hook 状態不明";
}

function coverageTitle(status: string) {
  return {
    available: "Usage を計測中",
    no_events: "Usage 計測を待機中",
    unavailable: "Usage は未対応",
  }[status] || "Usage 状態不明";
}

function ActivityChart({ days }: { days: DailyPoint[] }) {
  const max = Math.max(1, ...days.map((day) => Math.max(day.sessions, day.jobs, day.compactions)));
  if (!days.length) return <EmptyState message="日次データはありません。" />;
  return (
    <>
      <div className="chart-wrap">
        <div className="chart-y-labels"><span>{formatNumber(max)}</span><span>0</span></div>
        <div className="bar-chart" aria-label="Daily activity chart">
          {days.map((day) => (
            <div className="bar-group" key={day.day} title={`${day.day}: sessions ${day.sessions}, jobs ${day.jobs}, compactions ${day.compactions}`}>
              <div className="bar" style={{ height: `${day.sessions / max * 100}%` }} />
              <div className="bar mint" style={{ height: `${day.jobs / max * 100}%` }} />
              <div className="bar coral" style={{ height: `${day.compactions / max * 100}%` }} />
              <span className="bar-label">{formatDate(day.day)}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="chart-legend"><span><i className="legend-swatch indigo" />Sessions</span><span><i className="legend-swatch mint" />Jobs</span><span><i className="legend-swatch coral" />Compactions</span></div>
    </>
  );
}

