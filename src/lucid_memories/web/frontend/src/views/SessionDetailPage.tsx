import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import {
  DateCell,
  EmptyState,
  ExpandableText,
  Panel,
  PanelHeading,
  ResourceState,
  SessionMetaBadges,
  StatusBadge,
} from "../components";
import {
  formatCompactNumber,
  formatCost,
  formatDate,
  formatTokenCount,
  sessionDisplayTitle,
  shortId,
  storageScopeLabel,
} from "../format";
import { useRefresh, useResource } from "../hooks";
import { EventTable } from "./ActivityPage";
import { JobTable } from "./CollectionPages";
import type { SessionDetail } from "../types";

export function SessionDetailPage() {
  const { id = "" } = useParams();
  const { revision } = useRefresh();
  const resource = useResource((signal) => api.session(id, signal), [id, revision]);
  return (
    <div className="view">
      <div className="back-link">
        <Link to="/sessions">← セッション一覧に戻る</Link>
      </div>
      <ResourceState resource={resource}>
        {(detail) => <SessionDetailContent detail={detail} />}
      </ResourceState>
    </div>
  );
}

function SessionDetailContent({ detail }: { detail: SessionDetail }) {
  const session = detail.data;
  return (
    <div className="detail-stack">
      <Panel>
        <div className="panel-kicker">SESSION DETAIL</div>
        <h2>{sessionDisplayTitle(session.title, detail.input_output.input, session.conversation_id, session.brief)}</h2>
        <SessionMetaBadges session={session} />
        {(session.summary || session.brief) && (
          <p className="session-brief">{session.summary || session.brief}</p>
        )}
        {session.summary && session.brief && session.summary !== session.brief && (
          <p className="session-brief muted">ヒューリスティック: {session.brief}</p>
        )}
        <div className="detail-grid">
          <div><label>会話ID</label><p className="mono">{session.conversation_id}</p></div>
          {session.parent_conversation_id && (
            <div>
              <label>親セッション</label>
              <p>
                <Link className="link" to={`/sessions/${encodeURIComponent(session.parent_conversation_id)}`}>
                  {session.parent_title || shortId(session.parent_conversation_id)}
                </Link>
                <small className="mono">{session.parent_conversation_id}</small>
              </p>
            </div>
          )}
          <div><label>状態</label><p><StatusBadge value={session.status} /></p></div>
          <div><label>モデル</label><p>{session.model || "未記録"}</p></div>
          {session.composer_mode && <div><label>Composer</label><p>{session.composer_mode}</p></div>}
          <div><label>最終更新</label><p><DateCell value={session.updated_at} /></p></div>
        </div>
      </Panel>
      <Panel>
        <PanelHeading kicker="INPUT / OUTPUT" title="最新ターンの入出力内容" />
        <div className="content-columns">
          <TextBlock label="直前のプロンプト入力" value={detail.input_output.input} />
          <TextBlock label="モデル応答出力" value={detail.input_output.output} />
        </div>
        <p className="muted">{detail.input_output.message}</p>
      </Panel>
      <Panel>
        <PanelHeading kicker={`JOBS (${detail.relationships.jobs.length})`} title="関連ジョブ" />
        {detail.relationships.jobs.length ? <JobTable rows={detail.relationships.jobs} /> : <EmptyState message="関連するジョブ実行履歴はありません。" />}
      </Panel>
      <Panel>
        <PanelHeading kicker={`ACTIVITY (${detail.relationships.events.length})`} title="会話イベント履歴" />
        {detail.relationships.events.length ? <EventTable rows={detail.relationships.events} /> : <EmptyState message="会話イベントの記録はありません。" />}
      </Panel>
      <div className="content-grid">
        <Panel>
          <PanelHeading kicker={`USAGE (${detail.relationships.usage.length})`} title="トークン使用量 (Usage)" />
          {detail.relationships.usage.length ? (
            <ul className="detail-list">
              {detail.relationships.usage.map((event) => (
                <li key={event.id}>
                  {formatDate(event.created_at, true)} · {event.model || "未記録"} · in {formatTokenCount(event.input_tokens)} · out {formatTokenCount(event.output_tokens)} · {formatCost(event.cost_usd)}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState message="トークン使用量のイベント記録はありません。" />
          )}
        </Panel>
        <Panel>
          <PanelHeading kicker={`MCP (${detail.relationships.mcp?.length || 0})`} title="MCP 利用量" />
          {detail.relationships.mcp?.length ? (
            <ul className="detail-list">
              {detail.relationships.mcp.map((event) => (
                <li key={event.id}>
                  {formatDate(event.created_at, true)} · {event.tool_name || "未記録"} · {formatTokenCount(event.tokens ?? event.total_tokens)}
                  {event.is_large ? " · 大" : ""}
                  {event.budget ? ` / ${formatCompactNumber(event.budget)}` : ""}
                  {event.generation_id ? ` · ${event.generation_id.slice(0, 8)}` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState message="MCP 利用量の記録はありません。" />
          )}
        </Panel>
      </div>
      <div className="content-grid">
        <Panel>
          <PanelHeading kicker={`ARTIFACTS (${detail.relationships.artifacts.length})`} title="生成ファイル成果物" />
          {detail.relationships.artifacts.length ? (
            <ul className="detail-list">
              {detail.relationships.artifacts.map((artifact) => (
                <li key={artifact.id}>
                  <Link className="link" to={`/artifacts/${encodeURIComponent(artifact.id)}`}>
                    {artifact.relative_path || artifact.path || artifact.name}
                  </Link>
                  <small>
                    {storageScopeLabel(artifact.storage_scope)} · {artifact.content_availability || "unknown"} · {(artifact.blob_sha || artifact.sha256 || "").slice(0, 12) || "ハッシュなし"}
                  </small>
                  {artifact.content_preview && (
                    <details>
                      <summary>プレビューを表示</summary>
                      <pre>{artifact.content_preview}</pre>
                    </details>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState message="保存された成果物（ファイル）はありません。" />
          )}
        </Panel>
      </div>
      <Panel>
        <PanelHeading kicker={`COMPACTIONS (${detail.relationships.compactions.length})`} title="コンパクション (文脈圧縮)" />
        {detail.relationships.compactions.length ? (
          <ul className="detail-list">
            {detail.relationships.compactions.map((event) => (
              <li key={event.id}>
                {formatDate(event.created_at, true)} · {formatTokenCount(event.context_tokens)} · {event.context_usage_percent == null ? "—" : `${Math.round(event.context_usage_percent)}%`}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState message="コンパクション（文脈圧縮）のイベント記録はありません。" />
        )}
      </Panel>
    </div>
  );
}

function TextBlock({ label, value }: { label: string; value?: string | null }) {
  return <div className="text-block"><label>{label}</label><ExpandableText value={value || "保存された内容はありません。"} /></div>;
}

