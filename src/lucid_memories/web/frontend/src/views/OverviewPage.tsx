import { Link } from "react-router-dom";
import { api } from "../api";
import {
  DateCell,
  EmptyState,
  MetricCard,
  Panel,
  PanelHeading,
  ResourceState,
  SessionMetaBadges,
  StatusBadge,
  ViewIntro,
} from "../components";
import { formatCompactNumber, formatCost, formatDate, formatNumber, formatTokenCount, sessionDisplayTitle, shortId, truncate } from "../format";
import { useRefresh, useResource } from "../hooks";
import type { DailyPoint, McpSummary } from "../types";

export function OverviewPage() {
  const { revision } = useRefresh();
  const overview = useResource((signal) => api.overview(signal), [revision]);
  const daily = useResource((signal) => api.daily(14, signal), [revision]);

  return (
    <div className="view">
      <ViewIntro
        description="Cursor 上の会話セッション、ジョブキュー、コンパクション、想起ログ、MCP 利用量の健全性をリアルタイムに可視化します。"
        kicker="REAL-TIME MEMORY BUS"
        title="エージェント記憶と実行状況の統合ダッシュボード"
      />
      <ResourceState resource={overview}>
        {(data) => (
          <>
            <div className="metric-grid">
              <MetricCard label="アクティブセッション" note={`全 ${formatNumber(data.counts.sessions)} 件`} value={data.counts.live_sessions} />
              <MetricCard label="実行中ジョブ" note={`全 ${formatNumber(data.counts.jobs)} 件`} tone="mint" value={data.counts.open_jobs} />
              <MetricCard
                label="入力トークン"
                note={data.usage.status === "available" ? `計測イベント ${formatNumber(data.usage.events)} 件` : data.usage.status}
                tone="coral"
                title={data.usage.status === "available" ? `${formatNumber(data.usage.input_tokens)} tok` : undefined}
                value={data.usage.status === "available" ? formatTokenCount(data.usage.input_tokens) : "—"}
              />
              <MetricCard
                label="推定コスト (USD)"
                note={data.usage.status === "available" ? `出力 ${formatTokenCount(data.usage.output_tokens)}` : data.usage.status}
                tone="yellow"
                value={data.usage.status === "available" ? formatCost(data.usage.cost_usd) : "—"}
              />
            </div>
            <div className="content-grid">
              <Panel className="wide-panel">
                <PanelHeading kicker="ACTIVITY" title="日次アクティビティ" action={<span className="panel-note">直近 14 日間</span>} />
                <ActivityChart days={daily.data?.data || []} />
              </Panel>
              <Panel>
                <PanelHeading kicker="MCP USAGE" title="MCP 利用量" />
                <McpUsagePanel mcp={data.mcp} />
              </Panel>
            </div>
            <div className="content-grid">
              <Panel className="wide-panel">
                <PanelHeading kicker="DISTRIBUTION" title="モデル利用比率" />
                <div className="model-list">
                  {data.models.length ? data.models.slice(0, 6).map((model, index) => {
                    const total = data.models.reduce((sum, item) => sum + item.sessions, 0) || 1;
                    return (
                      <div className="model-row" key={model.model}>
                        <div className="model-top"><span>{model.model}</span><span className="model-count">{formatNumber(model.sessions)} sessions</span></div>
                        <div className="model-track"><div className={`model-fill fill-${index % 3}`} style={{ width: `${model.sessions / total * 100}%` }} /></div>
                      </div>
                    );
                  }) : <EmptyState message="モデルの利用記録はまだありません。" />}
                </div>
              </Panel>
            </div>
            <div className="content-grid lower-grid">
              <Panel className="wide-panel">
                <PanelHeading kicker="RECENT" title="最近のセッション" action={<Link className="text-link" to="/sessions">すべて表示 →</Link>} />
                <div className="session-list">
                  {data.recent_sessions.length ? data.recent_sessions.map((session) => (
                    <Link className="session-row" key={session.conversation_id} to={`/sessions/${encodeURIComponent(session.conversation_id)}`}>
                      <div className="session-row-main">
                        <strong>{sessionDisplayTitle(session.title, session.last_prompt, session.conversation_id, session.brief)}</strong>
                        <small className="mono">{shortId(session.conversation_id)}</small>
                        <SessionMetaBadges session={session} />
                        {(session.brief || session.last_prompt) && (
                          <p className="session-brief">{truncate(session.brief || session.last_prompt, 120)}</p>
                        )}
                      </div>
                      <span className="session-model">{session.model || "未記録"}</span>
                      <StatusBadge value={session.status} />
                      <DateCell value={session.last_heartbeat_at || session.updated_at} />
                    </Link>
                  )) : <EmptyState message="セッション履歴はまだありません。" />}
                </div>
              </Panel>
              <Panel>
                <PanelHeading kicker="DATA COVERAGE" title="トークン計測カバレッジ" />
                <div className={`coverage-card coverage-${data.usage.status}`}>
                  <div className="coverage-icon">{data.usage.status === "available" ? "◉" : "◒"}</div>
                  <h4>{coverageTitle(data.usage.status)}</h4>
                  <p>{data.usage.message}</p>
                  <div className="coverage-meter"><span style={{ width: data.usage.status === "available" ? "100%" : "40%" }} /></div>
                  <small>{formatNumber(data.index.events)} events · {formatNumber(data.index.artifacts)} artifacts</small>
                </div>
              </Panel>
            </div>
            <div className="content-grid lower-grid">
              <Panel>
                <PanelHeading kicker="HOOK COVERAGE" title="Hookイベント収集状況" />
                {data.hooks ? (
                  <div className={`coverage-card coverage-${data.hooks.status}`}>
                    <h4>{hookTitle(data.hooks.status)}</h4>
                    <p>{data.hooks.message}</p>
                    <small>
                      {formatNumber(data.hooks.events)} events
                      {data.hooks.missing.length ? ` · missing ${data.hooks.missing.slice(0, 3).join(", ")}` : ""}
                    </small>
                  </div>
                ) : <EmptyState message="Hook収集状況は未接続です。" />}
              </Panel>
              <Panel>
                <PanelHeading kicker="PERSONA" title="Global persona" />
                {data.persona ? (
                  <div className="status-summary">
                    <div className="status-summary-row"><span>Scope</span><strong>{data.persona.scope || "未設定"}</strong></div>
                    <StatusSummaryRow label="Sections" value={data.persona.sections} />
                    <StatusSummaryRow compact label="Token estimate" suffix={data.persona.token_budget ? `/ ${formatTokenCount(data.persona.token_budget)}` : "tok"} value={data.persona.token_estimate ?? undefined} />
                    <StatusSummaryRow label="Pending candidates" value={data.persona.candidates.counts.pending} />
                    <p className="muted">{data.persona.message}</p>
                  </div>
                ) : <EmptyState message="ペルソナは未設定です。" />}
              </Panel>
            </div>
            <div className="content-grid lower-grid">
              <Panel>
                <PanelHeading
                  kicker="MEMORY LIFECYCLE"
                  title="記憶ライフサイクル"
                  action={<Link className="text-link" to="/memory">詳細 →</Link>}
                />
                {data.memory ? (
                  <div className="status-summary">
                    <StatusSummaryRow label="有効な記憶 (Active)" value={data.memory.knowledge.counts.active} />
                    <StatusSummaryRow label="減衰した記憶 (Faded)" value={data.memory.knowledge.counts.faded} />
                    <StatusSummaryRow label="保留中タスク" value={data.memory.tasks.counts.pending} />
                    <StatusSummaryRow label="昇格候補 (Candidate)" value={data.memory.candidates.counts.pending} />
                  </div>
                ) : <EmptyState message="記憶ライフサイクルの情報はありません。" />}
              </Panel>
              <Panel>
                <PanelHeading kicker="INDEX HEALTH" title="検索・埋め込み・Map" />
                <div className="status-summary">
                  <StatusSummaryRow label="ベクトル埋め込み" value={data.embeddings?.total} suffix={data.embeddings?.available ? "vectors" : "未生成"} />
                  <StatusSummaryRow label="ナレッジマップノード" value={data.map?.nodes} suffix={data.map?.graph_available ? "graph ready" : "SQLite index"} />
                  <StatusSummaryRow label="提案中の関係エッジ" value={proposedEdgeCount(data.map?.relation_counts)} />
                  <StatusSummaryRow label="想起ヒット率" value={data.retrieval?.hit_rate == null ? undefined : Math.round(data.retrieval.hit_rate * 100)} suffix={data.retrieval?.hit_rate == null ? "—" : "%"} />
                  <StatusSummaryRow label="永続化Blob" value={data.storage?.blobs.count} suffix={data.storage?.blobs.available ? "blobs" : "未保存"} />
                  <StatusSummaryRow label="Relayパック容量" value={data.context?.token_estimate} compact suffix={data.context?.available ? "tok" : "未生成"} />
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

function McpUsagePanel({ mcp }: { mcp: McpSummary }) {
  if (!mcp.events) {
    return <EmptyState message={mcp.message || "MCP 利用量の記録はまだありません。"} />;
  }
  const stats = mcp.stats;
  const total = Math.max(1, mcp.tokens, ...mcp.by_tool.map((item) => item.tokens));
  return (
    <>
      <div className="status-summary">
        <StatusSummaryRow label="呼び出し" value={mcp.events} />
        <StatusSummaryRow compact label="結果トークン" suffix="tok" value={mcp.tokens} />
        {stats?.mean != null && <StatusSummaryRow compact label="平均" suffix="tok" value={stats.mean} />}
        {stats?.median != null && <StatusSummaryRow compact label="中央値" suffix="tok" value={stats.median} />}
        {stats?.p90 != null && stats.sample_size >= 5 && <StatusSummaryRow compact label="p90" suffix="tok" value={stats.p90} />}
        {stats?.large_threshold != null && (
          <StatusSummaryRow compact label="大きい目安" suffix={stats.large_label ? `tok (${stats.large_label})` : "tok"} value={stats.large_threshold} />
        )}
      </div>
      <div className="model-list">
        {mcp.by_tool.slice(0, 6).map((item, index) => (
          <div className="model-row" key={item.tool}>
            <div className="model-top">
              <span>{item.tool}</span>
              <span className="model-count">{formatTokenCount(item.tokens)} · {formatNumber(item.events)}</span>
            </div>
            <div className="model-track">
              <div className={`model-fill fill-${index % 3}`} style={{ width: `${item.tokens / total * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
      <p className="muted">{mcp.message}</p>
    </>
  );
}

function StatusSummaryRow({
  label,
  value,
  suffix,
  compact = false,
}: {
  label: string;
  value?: number;
  suffix?: string;
  compact?: boolean;
}) {
  const formatted = value == null ? "—" : compact ? formatCompactNumber(value) : formatNumber(value);
  return (
    <div className="status-summary-row">
      <span>{label}</span>
      <strong title={value == null ? undefined : formatNumber(value)}>{formatted} {suffix || ""}</strong>
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
    available: "Hook イベントを正常に収集中",
    partial: "一部の Hook イベントのみ収集中",
    no_events: "Hook イベントの受信待機中",
    unavailable: "Hook 未接続 / 未対応",
  }[status] || "Hook 状態不明";
}

function coverageTitle(status: string) {
  return {
    available: "トークン使用量を計測中",
    no_events: "トークン使用量の記録待機中",
    unavailable: "トークン使用量の計測なし",
  }[status] || "計測状態不明";
}

function ActivityChart({ days }: { days: DailyPoint[] }) {
  const max = Math.max(1, ...days.map((day) => Math.max(day.sessions, day.jobs, day.compactions)));
  if (!days.length) return <EmptyState message="日次アクティビティのデータはありません。" />;
  return (
    <>
      <div className="chart-wrap">
        <div className="chart-y-labels"><span>{formatNumber(max)}</span><span>0</span></div>
        <div className="bar-chart" aria-label="日次アクティビティチャート">
          {days.map((day) => (
            <div className="bar-group" key={day.day} title={`${day.day}: セッション ${day.sessions}, ジョブ ${day.jobs}, コンパクション ${day.compactions}`}>
              <div className="bar" style={{ height: `${day.sessions / max * 100}%` }} />
              <div className="bar mint" style={{ height: `${day.jobs / max * 100}%` }} />
              <div className="bar coral" style={{ height: `${day.compactions / max * 100}%` }} />
              <span className="bar-label">{formatDate(day.day)}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="chart-legend"><span><i className="legend-swatch indigo" />セッション</span><span><i className="legend-swatch mint" />ジョブ</span><span><i className="legend-swatch coral" />コンパクション</span></div>
    </>
  );
}

