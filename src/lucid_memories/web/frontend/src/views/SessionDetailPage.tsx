import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import {
  DateCell,
  EmptyState,
  ExpandableText,
  Panel,
  PanelHeading,
  ResourceState,
  StatusBadge,
} from "../components";
import { formatCost, formatDate, formatNumber, sessionDisplayTitle, storageScopeLabel } from "../format";
import { useRefresh, useResource } from "../hooks";
import { EventTable } from "./ActivityPage";
import { JobTable } from "./CollectionPages";
import type { SessionDetail } from "../types";

export function SessionDetailPage() {
  const { id = "" } = useParams();
  const { revision } = useRefresh();
  const resource = useResource((signal) => api.session(id, signal), [id, revision]);
  return <div className="view"><div className="back-link"><Link to="/sessions">← Sessions に戻る</Link></div><ResourceState resource={resource}>{(detail) => <SessionDetailContent detail={detail} />}</ResourceState></div>;
}

function SessionDetailContent({ detail }: { detail: SessionDetail }) {
  const session = detail.data;
  return <div className="detail-stack">
    <Panel><div className="panel-kicker">SESSION DETAIL</div><h2>{sessionDisplayTitle(session.title, detail.input_output.input, session.conversation_id)}</h2><div className="detail-grid">
      <div><label>Conversation ID</label><p className="mono">{session.conversation_id}</p></div><div><label>Status</label><p><StatusBadge value={session.status} /></p></div><div><label>Model</label><p>{session.model || "未記録"}</p></div><div><label>Updated</label><p><DateCell value={session.updated_at} /></p></div>
    </div></Panel>
    <Panel><PanelHeading kicker="INPUT / OUTPUT" title="Turn content" /><div className="content-columns"><TextBlock label="Last input" value={detail.input_output.input} /><TextBlock label="Output" value={detail.input_output.output} /></div><p className="muted">{detail.input_output.message}</p></Panel>
    <Panel><PanelHeading kicker={`JOBS (${detail.relationships.jobs.length})`} title="Related jobs" />{detail.relationships.jobs.length ? <JobTable rows={detail.relationships.jobs} /> : <EmptyState message="関連ジョブはありません。" />}</Panel>
    <Panel><PanelHeading kicker={`ACTIVITY (${detail.relationships.events.length})`} title="Conversation events" />{detail.relationships.events.length ? <EventTable rows={detail.relationships.events} /> : <EmptyState message="会話イベントはありません。" />}</Panel>
    <div className="content-grid">
      <Panel><PanelHeading kicker={`USAGE (${detail.relationships.usage.length})`} title="Usage" />{detail.relationships.usage.length ? <ul className="detail-list">{detail.relationships.usage.map((event) => <li key={event.id}>{formatDate(event.created_at, true)} · {event.model || "未記録"} · in {formatNumber(event.input_tokens)} · out {formatNumber(event.output_tokens)} · {formatCost(event.cost_usd)}</li>)}</ul> : <EmptyState message="Usage event はありません。" />}</Panel>
      <Panel><PanelHeading kicker={`ARTIFACTS (${detail.relationships.artifacts.length})`} title="Artifacts" />{detail.relationships.artifacts.length ? <ul className="detail-list">{detail.relationships.artifacts.map((artifact) => <li key={artifact.id}><Link className="link" to={`/artifacts/${encodeURIComponent(artifact.id)}`}>{artifact.relative_path || artifact.path || artifact.name}</Link><small>{storageScopeLabel(artifact.storage_scope)} · {artifact.content_availability || "unknown"} · {(artifact.blob_sha || artifact.sha256 || "").slice(0, 12) || "hash unavailable"}</small>{artifact.content_preview && <details><summary>Preview</summary><pre>{artifact.content_preview}</pre></details>}</li>)}</ul> : <EmptyState message="ファイル成果物はありません。" />}</Panel>
    </div>
    <Panel><PanelHeading kicker={`COMPACTIONS (${detail.relationships.compactions.length})`} title="Compactions" />{detail.relationships.compactions.length ? <ul className="detail-list">{detail.relationships.compactions.map((event) => <li key={event.id}>{formatDate(event.created_at, true)} · {formatNumber(event.context_tokens)} tokens · {event.context_usage_percent == null ? "—" : `${Math.round(event.context_usage_percent)}%`}</li>)}</ul> : <EmptyState message="Compaction event はありません。" />}</Panel>
  </div>;
}

function TextBlock({ label, value }: { label: string; value?: string | null }) {
  return <div className="text-block"><label>{label}</label><ExpandableText value={value || "保存された内容はありません。"} /></div>;
}

