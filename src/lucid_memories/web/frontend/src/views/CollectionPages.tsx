import { api } from "../api";
import {
  DateCell,
  EmptyState,
  Pagination,
  Panel,
  ResourceState,
  SearchInput,
  SessionLink,
  SessionMetaBadges,
  StatusBadge,
  ViewIntro,
} from "../components";
import { formatCompactNumber, formatCost, formatNumber, sessionDisplayTitle, shortId, truncate } from "../format";
import { useDebouncedValue, usePageQuery, useRefresh, useResource } from "../hooks";
import type { DailyPoint, Job, Session } from "../types";

export function SessionsPage() {
  const { searchParams, setValue } = usePageQuery();
  const { revision } = useRefresh();
  const q = searchParams.get("q") || "";
  const status = searchParams.get("status") || "";
  const kind = searchParams.get("kind") || "";
  const origin = searchParams.get("origin") || "";
  const model = searchParams.get("model") || "";
  const from = searchParams.get("from") || "";
  const to = searchParams.get("to") || "";
  const page = Number(searchParams.get("page") || "1");
  const debouncedQ = useDebouncedValue(q);
  const resource = useResource(
    (signal) => api.sessions({ q: debouncedQ, status, kind, origin, model, from, to, page, limit: 25 }, signal),
    [debouncedQ, status, kind, origin, model, from, to, page, revision],
  );
  return (
    <div className="view">
      <ViewIntro
        description="Cursor IDE 内の会話セッション単位の状態、利用モデル、直近のプロンプト入力、および関連ジョブの追跡一覧です。"
        kicker="CONVERSATIONS"
        title="会話セッション履歴 (Sessions)"
      >
        <SearchInput label="セッションを検索" onChange={(value) => setValue("q", value)} placeholder="タイトル・モデル・入力を検索" value={q} />
      </ViewIntro>
      <Panel className="filter-panel">
        <div className="filter-row">
          <label>
            状態
            <select onChange={(event) => setValue("status", event.target.value)} value={status}>
              <option value="">すべて</option>
              <option value="active">アクティブ (active)</option>
              <option value="idle">アイドル (idle)</option>
              <option value="done">完了 (done)</option>
              <option value="stale">停止 / ステール (stale)</option>
            </select>
          </label>
          <label>
            種別
            <select onChange={(event) => setValue("kind", event.target.value)} value={kind}>
              <option value="">すべて</option>
              <option value="main">メイン</option>
              <option value="sub">サブ</option>
              <option value="background">バックグラウンド</option>
            </select>
          </label>
          <label>
            起点
            <select onChange={(event) => setValue("origin", event.target.value)} value={origin}>
              <option value="">すべて</option>
              <option value="human">人間起点</option>
              <option value="agent">Agent起点</option>
              <option value="unknown">起点不明</option>
            </select>
          </label>
          <label>モデル<input onChange={(event) => setValue("model", event.target.value)} placeholder="例: gpt-4o, claude-3-7..." value={model} /></label>
          <label>開始日<input onChange={(event) => setValue("from", event.target.value)} type="date" value={from} /></label>
          <label>終了日<input onChange={(event) => setValue("to", event.target.value)} type="date" value={to} /></label>
        </div>
      </Panel>
      <Panel className="table-panel">
        <ResourceState resource={resource}>
          {(payload) => (
            <>
              {payload.data.length ? <SessionTable rows={payload.data} /> : <EmptyState message="該当するセッションは見つかりませんでした。" />}
              <Pagination onChange={(next) => setValue("page", next, false)} page={payload.pagination} />
            </>
          )}
        </ResourceState>
      </Panel>
    </div>
  );
}

function SessionTable({ rows }: { rows: Session[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>セッション</th>
            <th>種別</th>
            <th>状態</th>
            <th>モデル</th>
            <th>概要</th>
            <th>ジョブ数</th>
            <th>最終更新</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((session) => (
            <tr key={session.conversation_id}>
              <td className="primary">
                <SessionLink
                  fallback={session.brief || session.last_prompt}
                  id={session.conversation_id}
                  title={sessionDisplayTitle(session.title, session.last_prompt, session.conversation_id, session.brief)}
                />
                <small className="mono">{shortId(session.conversation_id)}</small>
                {session.parent_conversation_id && (
                  <small className="session-parent-link">
                    親:{" "}
                    <Link className="link" to={`/sessions/${encodeURIComponent(session.parent_conversation_id)}`}>
                      {session.parent_title || shortId(session.parent_conversation_id)}
                    </Link>
                  </small>
                )}
              </td>
              <td><SessionMetaBadges session={session} /></td>
              <td><StatusBadge value={session.status} /></td>
              <td className="mono">{session.model || "未記録"}</td>
              <td className="truncate" title={session.brief || session.last_prompt || ""}>
                {truncate(session.brief || session.last_prompt, 100) || "—"}
              </td>
              <td className="mono">{formatNumber(session.job_count)}</td>
              <td><DateCell value={session.updated_at} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function JobsPage() {
  const { searchParams, setValue } = usePageQuery();
  const { revision } = useRefresh();
  const q = searchParams.get("q") || "";
  const status = searchParams.get("status") || "";
  const from = searchParams.get("from") || "";
  const to = searchParams.get("to") || "";
  const page = Number(searchParams.get("page") || "1");
  const debouncedQ = useDebouncedValue(q);
  const resource = useResource((signal) => api.jobs({ q: debouncedQ, status, from, to, page, limit: 25 }, signal), [debouncedQ, status, from, to, page, revision]);
  return (
    <div className="view">
      <ViewIntro
        description="サブエージェント実行や非同期処理として記録されたジョブの実行状況と結果を確認します。"
        kicker="WORK QUEUE"
        title="ジョブキュー履歴 (Jobs)"
      >
        <SearchInput label="ジョブを検索" onChange={(value) => setValue("q", value)} placeholder="ジョブ名・セッションを検索" value={q} />
      </ViewIntro>
      <Panel className="filter-panel">
        <div className="filter-row">
          <label>
            状態
            <select onChange={(event) => setValue("status", event.target.value)} value={status}>
              <option value="">すべての状態</option>
              <option value="pending">待機中 (pending)</option>
              <option value="running">実行中 (running)</option>
              <option value="blocked">中断 / ブロック (blocked)</option>
              <option value="done">完了 (done)</option>
              <option value="error">エラー (error)</option>
              <option value="stale">停止 / ステール (stale)</option>
            </select>
          </label>
          <label>開始日<input onChange={(event) => setValue("from", event.target.value)} type="date" value={from} /></label>
          <label>終了日<input onChange={(event) => setValue("to", event.target.value)} type="date" value={to} /></label>
        </div>
      </Panel>
      <Panel className="table-panel">
        <ResourceState resource={resource}>
          {(payload) => (
            <>
              {payload.data.length ? <JobTable rows={payload.data} /> : <EmptyState message="該当するジョブは見つかりませんでした。" />}
              <Pagination onChange={(next) => setValue("page", next, false)} page={payload.pagination} />
            </>
          )}
        </ResourceState>
      </Panel>
    </div>
  );
}

export function JobTable({ rows }: { rows: Job[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>ジョブ名</th>
            <th>種別</th>
            <th>状態</th>
            <th>関連セッション</th>
            <th>モデル</th>
            <th>最終更新</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((job) => (
            <tr key={job.id}>
              <td className="primary">
                <strong>{job.title || "無題のジョブ"}</strong>
                <small>{shortId(job.id)}</small>
              </td>
              <td className="mono">{job.kind || "—"}</td>
              <td><StatusBadge value={job.status} /></td>
              <td><SessionLink id={job.conversation_id} title={job.session_title} /></td>
              <td className="mono">{job.session_model || "未記録"}</td>
              <td><DateCell value={job.updated_at} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DailyPage() {
  const { searchParams, setValue } = usePageQuery();
  const { revision } = useRefresh();
  const days = Number(searchParams.get("days") || "14");
  const resource = useResource((signal) => api.daily(days, signal), [days, revision]);
  return (
    <div className="view">
      <ViewIntro
        description="セッション数、ジョブ実行数、コンパクション、想起イベント、MCP 利用量、Relayパックの日次推移レポートです。"
        kicker="TREND REPORT"
        title="日次アクティビティ集計 (Daily)"
      >
        <label className="select-control">
          集計期間
          <select onChange={(event) => setValue("days", event.target.value, false)} value={days}>
            <option value="7">直近 7 日間</option>
            <option value="14">直近 14 日間</option>
            <option value="30">直近 30 日間</option>
            <option value="60">直近 60 日間</option>
          </select>
        </label>
      </ViewIntro>
      <Panel className="table-panel">
        <ResourceState resource={resource}>
          {(payload) => payload.data.length ? <DailyTable rows={payload.data} /> : <EmptyState message="日次データはありません。" />}
        </ResourceState>
      </Panel>
    </div>
  );
}

function DailyTable({ rows }: { rows: DailyPoint[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>日付</th>
            <th>セッション</th>
            <th>ジョブ</th>
            <th>完了ジョブ</th>
            <th>コンパクション</th>
            <th>文脈トークン</th>
            <th>入力トークン</th>
            <th>出力トークン</th>
            <th>キャッシュ</th>
            <th>推定コスト</th>
            <th>MCP 回数</th>
            <th>MCP トークン</th>
            <th>想起回数</th>
            <th>Relayパック</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice().reverse().map((day) => (
            <tr key={day.day}>
              <td className="primary mono">{day.day}</td>
              <td className="mono">{formatNumber(day.sessions)}</td>
              <td className="mono">{formatNumber(day.jobs)}</td>
              <td className="mono">{formatNumber(day.completed_jobs)}</td>
              <td className="mono">{formatNumber(day.compactions)}</td>
              <td className="mono" title={formatNumber(day.context_tokens)}>{formatCompactNumber(day.context_tokens)}</td>
              <td className="mono" title={formatNumber(day.input_tokens)}>{formatCompactNumber(day.input_tokens)}</td>
              <td className="mono" title={formatNumber(day.output_tokens)}>{formatCompactNumber(day.output_tokens)}</td>
              <td className="mono" title={formatNumber(day.cached_tokens)}>{formatCompactNumber(day.cached_tokens)}</td>
              <td className="mono">{formatCost(day.cost_usd)}</td>
              <td className="mono">{formatNumber(day.mcp_calls)}</td>
              <td className="mono" title={formatNumber(day.mcp_tokens)}>{formatCompactNumber(day.mcp_tokens)}</td>
              <td className="mono">{formatNumber(day.retrievals)}</td>
              <td className="mono">{formatNumber(day.packs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

