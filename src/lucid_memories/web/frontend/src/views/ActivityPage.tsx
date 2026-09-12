import { Link } from "react-router-dom";
import { api } from "../api";
import {
  DateCell,
  EmptyState,
  ExpandableText,
  Pagination,
  Panel,
  PanelHeading,
  ResourceState,
  SearchInput,
  SessionLink,
  StatusBadge,
  ViewIntro,
} from "../components";
import { contentAvailabilityLabel, formatBytes, storageScopeLabel } from "../format";
import { useDebouncedValue, usePageQuery, useRefresh, useResource } from "../hooks";
import type { Artifact, ConversationEvent } from "../types";

export function ActivityPage() {
  const { searchParams, setValue } = usePageQuery();
  const { revision } = useRefresh();
  const q = searchParams.get("q") || "";
  const from = searchParams.get("from") || "";
  const to = searchParams.get("to") || "";
  const scope = searchParams.get("scope") || "";
  const page = Number(searchParams.get("page") || "1");
  const debouncedQ = useDebouncedValue(q);
  const events = useResource(
    (signal) => api.events({ q: debouncedQ, from, to, page, limit: 25 }, signal),
    [debouncedQ, from, to, page, revision],
  );
  const artifacts = useResource(
    (signal) => api.artifacts({ q: debouncedQ, from, to, scope, page, limit: 25 }, signal),
    [debouncedQ, from, to, scope, page, revision],
  );

  return (
    <div className="view">
      <ViewIntro
        description="会話イベント、ツール実行ログ、および生成されたファイル成果物を conversation_id 単位で追跡します。"
        kicker="TRACEABILITY INDEX"
        title="アクティビティ追跡 (Activity Index)"
      >
        <SearchInput
          label="アクティビティを検索"
          onChange={(value) => setValue("q", value)}
          placeholder="会話・ファイル・出力を検索..."
          value={q}
        />
      </ViewIntro>
      <Panel className="filter-panel">
        <div className="filter-row">
          <label>
            開始日
            <input onChange={(event) => setValue("from", event.target.value)} type="date" value={from} />
          </label>
          <label>
            終了日
            <input onChange={(event) => setValue("to", event.target.value)} type="date" value={to} />
          </label>
        </div>
      </Panel>
      <div className="index-grid">
        <Panel className="table-panel">
          <PanelHeading
            kicker="EVENTS"
            title="会話およびツール実行イベント"
            action={<span className="panel-note">全 {events.data?.pagination.total ?? "—"} 件</span>}
          />
          <ResourceState resource={events}>
            {(payload) => (
              <>
                {payload.data.length ? <EventTable rows={payload.data} /> : <EmptyState message="会話イベントは記録されていません。Hook 未接続または処理待ちの可能性があります。" />}
                <Pagination onChange={(next) => setValue("page", next, false)} page={payload.pagination} />
              </>
            )}
          </ResourceState>
        </Panel>
        <Panel className="table-panel">
          <PanelHeading
            kicker="ARTIFACTS"
            title="生成ファイル成果物"
            action={<span className="panel-note">全 {artifacts.data?.pagination.total ?? "—"} 件</span>}
          />
          <div className="table-toolbar">
            <label>
              保管スコープ
              <select onChange={(event) => setValue("scope", event.target.value)} value={scope}>
                <option value="">すべての保管先</option>
                <option value="conversation">conversation blob (セッション内Blob)</option>
                <option value="workspace">workspace file (ワークスペースファイル)</option>
                <option value="repository">repository file (リポジトリファイル)</option>
              </select>
            </label>
          </div>
          <ResourceState resource={artifacts}>
            {(payload) => (
              <>
                {payload.data.length ? <ArtifactTable rows={payload.data} /> : <EmptyState message="ファイル成果物は記録されていません。" />}
                <Pagination onChange={(next) => setValue("page", next, false)} page={payload.pagination} />
              </>
            )}
          </ResourceState>
        </Panel>
      </div>
    </div>
  );
}

export function EventTable({ rows }: { rows: ConversationEvent[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table event-table">
        <thead>
          <tr>
            <th>発生日時</th>
            <th>ロール / ツール</th>
            <th>関連セッション</th>
            <th>入力 (Input)</th>
            <th>出力 (Output)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((event) => (
            <tr key={event.id}>
              <td><DateCell value={event.created_at} /></td>
              <td className="primary">
                <StatusBadge value={event.status || event.role || event.event_type} />
                <small>{event.tool_name || event.event_type || "event"}</small>
              </td>
              <td><SessionLink id={event.conversation_id} title={event.session_title} /></td>
              <td><ExpandableText value={event.input_text} /></td>
              <td><ExpandableText value={event.output_text} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ArtifactTable({ rows }: { rows: Artifact[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table artifact-table">
        <thead>
          <tr>
            <th>更新日時</th>
            <th>ファイル名 / パス</th>
            <th>保管場所</th>
            <th>可用性</th>
            <th>関連セッション</th>
            <th>サイズ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((artifact) => (
            <tr key={artifact.id}>
              <td><DateCell value={artifact.updated_at} /></td>
              <td className="primary">
                <Link className="link" to={`/artifacts/${encodeURIComponent(artifact.id)}`}>
                  {artifact.relative_path || artifact.name || artifact.path || "—"}
                </Link>
                <small>{artifact.mime_type || "file"}</small>
              </td>
              <td>
                <span className="status-pill">{storageScopeLabel(artifact.storage_scope)}</span>
                <small>{artifact.storage_policy || (artifact.blob_sha ? "content-addressed" : "external file")}</small>
              </td>
              <td>
                <StatusBadge value={contentAvailabilityLabel(artifact.content_availability)} />
                <small>{artifact.path_exists ? "path present" : artifact.blob_recorded ? "blob recorded" : "index only"}</small>
              </td>
              <td><SessionLink id={artifact.conversation_id} title={artifact.session_title} /></td>
              <td className="mono">{artifact.size_bytes == null ? "—" : formatBytes(artifact.size_bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
