import { api } from "../api";
import {
  DateCell,
  EmptyState,
  Pagination,
  Panel,
  ResourceState,
  SearchInput,
  SessionLink,
  StatusBadge,
  ViewIntro,
} from "../components";
import { formatCost, formatNumber, sessionDisplayTitle, shortId, truncate } from "../format";
import { useDebouncedValue, usePageQuery, useRefresh, useResource } from "../hooks";
import type { DailyPoint, Job, Session } from "../types";

export function SessionsPage() {
  const { searchParams, setValue } = usePageQuery();
  const { revision } = useRefresh();
  const q = searchParams.get("q") || "";
  const status = searchParams.get("status") || "";
  const model = searchParams.get("model") || "";
  const from = searchParams.get("from") || "";
  const to = searchParams.get("to") || "";
  const page = Number(searchParams.get("page") || "1");
  const debouncedQ = useDebouncedValue(q);
  const resource = useResource(
    (signal) => api.sessions({ q: debouncedQ, status, model, from, to, page, limit: 25 }, signal),
    [debouncedQ, status, model, from, to, page, revision],
  );
  return <div className="view">
    <ViewIntro description="会話単位の状態、モデル、直近の入力、関連ジョブを確認します。" kicker="CONVERSATIONS" title="Session history">
      <SearchInput label="セッションを検索" onChange={(value) => setValue("q", value)} placeholder="タイトル・モデル・入力を検索" value={q} />
    </ViewIntro>
    <Panel className="filter-panel"><div className="filter-row">
      <label>Status<select onChange={(event) => setValue("status", event.target.value)} value={status}><option value="">すべて</option><option value="active">active</option><option value="idle">idle</option><option value="done">done</option><option value="stale">stale</option></select></label>
      <label>Model<input onChange={(event) => setValue("model", event.target.value)} placeholder="model-a" value={model} /></label>
      <label>From<input onChange={(event) => setValue("from", event.target.value)} type="date" value={from} /></label>
      <label>To<input onChange={(event) => setValue("to", event.target.value)} type="date" value={to} /></label>
    </div></Panel>
    <Panel className="table-panel"><ResourceState resource={resource}>{(payload) => <>{payload.data.length ? <SessionTable rows={payload.data} /> : <EmptyState message="該当するセッションはありません。" />}<Pagination onChange={(next) => setValue("page", next, false)} page={payload.pagination} /></>}</ResourceState></Panel>
  </div>;
}

function SessionTable({ rows }: { rows: Session[] }) {
  return <div className="table-scroll"><table className="data-table"><thead><tr><th>Session</th><th>Status</th><th>Model</th><th>Last input</th><th>Jobs</th><th>Updated</th></tr></thead><tbody>{rows.map((session) => <tr key={session.conversation_id}>
    <td className="primary"><SessionLink id={session.conversation_id} title={session.title} fallback={session.last_prompt} /><small>{shortId(session.conversation_id)}</small></td><td><StatusBadge value={session.status} /></td><td className="mono">{session.model || "未記録"}</td><td className="truncate" title={session.last_prompt || ""}>{truncate(session.last_prompt, 100)}</td><td className="mono">{formatNumber(session.job_count)}</td><td><DateCell value={session.updated_at} /></td>
  </tr>)}</tbody></table></div>;
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
  return <div className="view">
    <ViewIntro description="lucid-memories に記録されたジョブとサブエージェントの動きを確認します。" kicker="WORK QUEUE" title="Job history">
      <SearchInput label="ジョブを検索" onChange={(value) => setValue("q", value)} placeholder="ジョブ・session を検索" value={q} />
    </ViewIntro>
    <Panel className="filter-panel"><div className="filter-row"><label>Status<select onChange={(event) => setValue("status", event.target.value)} value={status}><option value="">すべての状態</option><option value="pending">pending</option><option value="running">running</option><option value="blocked">blocked</option><option value="done">done</option><option value="error">error</option><option value="stale">stale</option></select></label><label>From<input onChange={(event) => setValue("from", event.target.value)} type="date" value={from} /></label><label>To<input onChange={(event) => setValue("to", event.target.value)} type="date" value={to} /></label></div></Panel>
    <Panel className="table-panel"><ResourceState resource={resource}>{(payload) => <>{payload.data.length ? <JobTable rows={payload.data} /> : <EmptyState message="該当するジョブはありません。" />}<Pagination onChange={(next) => setValue("page", next, false)} page={payload.pagination} /></>}</ResourceState></Panel>
  </div>;
}

export function JobTable({ rows }: { rows: Job[] }) {
  return <div className="table-scroll"><table className="data-table"><thead><tr><th>Job</th><th>Type</th><th>Status</th><th>Session</th><th>Model</th><th>Updated</th></tr></thead><tbody>{rows.map((job) => <tr key={job.id}>
    <td className="primary"><strong>{job.title || "Untitled job"}</strong><small>{shortId(job.id)}</small></td><td className="mono">{job.kind || "—"}</td><td><StatusBadge value={job.status} /></td><td><SessionLink id={job.conversation_id} title={job.session_title} /></td><td className="mono">{job.session_model || "未記録"}</td><td><DateCell value={job.updated_at} /></td>
  </tr>)}</tbody></table></div>;
}

export function DailyPage() {
  const { searchParams, setValue } = usePageQuery();
  const { revision } = useRefresh();
  const days = Number(searchParams.get("days") || "14");
  const resource = useResource((signal) => api.daily(days, signal), [days, revision]);
  return <div className="view">
    <ViewIntro description="セッション、ジョブ、コンパクション、検索、pack の日次集計です。" kicker="TREND REPORT" title="Daily summary">
      <label className="select-control">Period<select onChange={(event) => setValue("days", event.target.value, false)} value={days}><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option><option value="60">60 days</option></select></label>
    </ViewIntro>
    <Panel className="table-panel"><ResourceState resource={resource}>{(payload) => payload.data.length ? <DailyTable rows={payload.data} /> : <EmptyState message="日次データはありません。" />}</ResourceState></Panel>
  </div>;
}

function DailyTable({ rows }: { rows: DailyPoint[] }) {
  return <div className="table-scroll"><table className="data-table"><thead><tr><th>Date</th><th>Sessions</th><th>Jobs</th><th>Done</th><th>Compactions</th><th>Context</th><th>Input</th><th>Output</th><th>Cached</th><th>Cost</th><th>Retrievals</th><th>Packs</th></tr></thead><tbody>{rows.slice().reverse().map((day) => <tr key={day.day}>
    <td className="primary mono">{day.day}</td><td className="mono">{formatNumber(day.sessions)}</td><td className="mono">{formatNumber(day.jobs)}</td><td className="mono">{formatNumber(day.completed_jobs)}</td><td className="mono">{formatNumber(day.compactions)}</td><td className="mono">{formatNumber(day.context_tokens)}</td><td className="mono">{formatNumber(day.input_tokens)}</td><td className="mono">{formatNumber(day.output_tokens)}</td><td className="mono">{formatNumber(day.cached_tokens)}</td><td className="mono">{formatCost(day.cost_usd)}</td><td className="mono">{formatNumber(day.retrievals)}</td><td className="mono">{formatNumber(day.packs)}</td>
  </tr>)}</tbody></table></div>;
}

