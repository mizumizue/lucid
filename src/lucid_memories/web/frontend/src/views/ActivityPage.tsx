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
        description="会話、tool 出力、生成ファイルを conversation_id で追跡します。本文の正本が lucid-memories か Workspace / Repository かも区別します。"
        kicker="TRACEABILITY INDEX"
        title="Activity index"
      >
        <SearchInput
          label="アクティビティを検索"
          onChange={(value) => setValue("q", value)}
          placeholder="会話・ファイル・出力を検索"
          value={q}
        />
      </ViewIntro>
      <Panel className="filter-panel">
        <div className="filter-row">
          <label>
            From
            <input onChange={(event) => setValue("from", event.target.value)} type="date" value={from} />
          </label>
          <label>
            To
            <input onChange={(event) => setValue("to", event.target.value)} type="date" value={to} />
          </label>
        </div>
      </Panel>
      <div className="index-grid">
        <Panel className="table-panel">
          <PanelHeading
            kicker="EVENTS"
            title="会話と生成イベント"
            action={<span className="panel-note">{events.data?.pagination.total ?? "—"} events</span>}
          />
          <ResourceState resource={events}>
            {(payload) => (
              <>
                {payload.data.length ? <EventTable rows={payload.data} /> : <EmptyState message="会話イベントはありません。hook 未接続の可能性があります。" />}
                <Pagination onChange={(next) => setValue("page", next, false)} page={payload.pagination} />
              </>
            )}
          </ResourceState>
        </Panel>
        <Panel className="table-panel">
          <PanelHeading
            kicker="ARTIFACTS"
            title="出力ファイル / 成果物"
            action={<span className="panel-note">{artifacts.data?.pagination.total ?? "—"} files</span>}
          />
          <div className="table-toolbar">
            <label>
              Storage
              <select onChange={(event) => setValue("scope", event.target.value)} value={scope}>
                <option value="">すべての保管先</option>
                <option value="conversation">conversation blob</option>
                <option value="workspace">workspace file</option>
                <option value="repository">repository file</option>
              </select>
            </label>
          </div>
          <ResourceState resource={artifacts}>
            {(payload) => (
              <>
                {payload.data.length ? <ArtifactTable rows={payload.data} /> : <EmptyState message="ファイル成果物はありません。" />}
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
            <th>Time</th>
            <th>Role / tool</th>
            <th>Session</th>
            <th>Input</th>
            <th>Output</th>
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
            <th>Updated</th>
            <th>File</th>
            <th>Storage</th>
            <th>Availability</th>
            <th>Session</th>
            <th>Size</th>
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
