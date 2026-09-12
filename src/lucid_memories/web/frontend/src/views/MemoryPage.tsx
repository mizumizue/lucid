import type { ReactNode } from "react";
import { api } from "../api";
import {
  DateCell,
  EmptyState,
  ExpandableText,
  MetricCard,
  Panel,
  PanelHeading,
  Pagination,
  ResourceState,
  SearchInput,
  SessionLink,
  StatusBadge,
  ViewIntro,
} from "../components";
import { formatNumber } from "../format";
import { useDebouncedValue, usePageQuery, useRefresh, useResource } from "../hooks";
import type { Knowledge, MemoryCandidate, MemoryStatus, MemoryTask } from "../types";

const TABS = [
  { id: "knowledge", label: "Knowledge" },
  { id: "candidates", label: "Candidates" },
  { id: "tasks", label: "Tasks" },
] as const;

type MemoryTab = (typeof TABS)[number]["id"];

export function MemoryPage() {
  const { searchParams, setValue, replaceQuery } = usePageQuery();
  const { revision } = useRefresh();
  const tab = (searchParams.get("tab") || "knowledge") as MemoryTab;
  const statusParam = searchParams.get("status");
  const status = statusParam === null ? defaultStatus(tab) : statusParam;
  const q = searchParams.get("q") || "";
  const page = Number(searchParams.get("page") || "1");
  const debouncedQ = useDebouncedValue(q);
  const summary = useResource((signal) => api.memoryStatus(signal), [revision]);
  const query = { q: debouncedQ, status, page, limit: 25 };
  const knowledge = useResource(
    (signal) => tab === "knowledge"
      ? api.knowledge(query, signal)
      : Promise.resolve(emptyList),
    [tab, debouncedQ, status, page, revision],
  );
  const candidates = useResource(
    (signal) => tab === "candidates"
      ? api.memoryCandidates(query, signal)
      : Promise.resolve(emptyList),
    [tab, debouncedQ, status, page, revision],
  );
  const tasks = useResource(
    (signal) => tab === "tasks"
      ? api.memoryTasks(query, signal)
      : Promise.resolve(emptyList),
    [tab, debouncedQ, status, page, revision],
  );

  return (
    <div className="view">
      <ViewIntro
        description="長期記憶、昇格待ちの候補、統合タスクを読み取り専用で確認します。"
        kicker="MEMORY LIFECYCLE"
        title="Memory operations"
      >
        <SearchInput
          label="記憶を検索"
          onChange={(value) => setValue("q", value)}
          placeholder="タイトル・本文・会話を検索"
          value={q}
        />
      </ViewIntro>
      <ResourceState resource={summary}>
        {(data) => <MemorySummary status={data} />}
      </ResourceState>
      <div className="tab-row" role="tablist">
        {TABS.map((item) => (
          <button
            aria-selected={tab === item.id}
            className={`tab-button${tab === item.id ? " active" : ""}`}
            key={item.id}
            onClick={() => replaceQuery({ tab: item.id })}
            role="tab"
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>
      <Panel className="filter-panel">
        <div className="filter-row">
          <label>
            Status
            <select onChange={(event) => setValue("status", event.target.value)} value={status}>
              {statusOptions(tab).map((option) => (
                <option key={option.value || "all"} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
        </div>
      </Panel>
      {tab === "knowledge" && (
        <CollectionPanel
          action={knowledge.data?.pagination.total}
          kicker="LONG-TERM MEMORY"
          title="Knowledge"
          resource={knowledge}
          empty="該当する knowledge はありません。"
          table={(rows: Knowledge[]) => <KnowledgeTable rows={rows} />}
          onPage={(next) => setValue("page", next, false)}
        />
      )}
      {tab === "candidates" && (
        <CollectionPanel
          action={candidates.data?.pagination.total}
          kicker="CANDIDATE QUEUE"
          title="Memory candidates"
          resource={candidates}
          empty="該当する memory candidate はありません。"
          table={(rows: MemoryCandidate[]) => <CandidateTable rows={rows} />}
          onPage={(next) => setValue("page", next, false)}
        />
      )}
      {tab === "tasks" && (
        <CollectionPanel
          action={tasks.data?.pagination.total}
          kicker="CONSOLIDATION QUEUE"
          title="Memory tasks"
          resource={tasks}
          empty="該当する memory task はありません。"
          table={(rows: MemoryTask[]) => <TaskTable rows={rows} />}
          onPage={(next) => setValue("page", next, false)}
        />
      )}
    </div>
  );
}

function CollectionPanel<T>({
  kicker,
  title,
  action,
  resource,
  empty,
  table,
  onPage,
}: {
  kicker: string;
  title: string;
  action?: number;
  resource: ReturnType<typeof useResource<{ data: T[]; pagination: { page: number; pages: number; total: number; has_previous: boolean; has_next: boolean } }>>;
  empty: string;
  table: (rows: T[]) => ReactNode;
  onPage: (page: number) => void;
}) {
  return (
    <Panel className="table-panel">
      <PanelHeading
        kicker={kicker}
        title={title}
        action={<span className="panel-note">{action ?? "—"} records</span>}
      />
      <ResourceState resource={resource}>
        {(payload) => (
          <>
            {payload.data.length ? table(payload.data) : <EmptyState message={empty} />}
            <Pagination onChange={onPage} page={payload.pagination} />
          </>
        )}
      </ResourceState>
    </Panel>
  );
}

function MemorySummary({ status }: { status: MemoryStatus }) {
  const knowledge = status.knowledge;
  const tasks = status.tasks;
  const candidates = status.candidates;
  return (
    <div className="metric-grid memory-metric-grid">
      <MetricCard
        label="Active memories"
        note={`${formatNumber(knowledge.total)} total knowledge`}
        value={knowledge.counts.active || 0}
      />
      <MetricCard
        label="Pending tasks"
        note={`${formatNumber(tasks.total)} consolidation tasks`}
        tone="mint"
        value={tasks.counts.pending || 0}
      />
      <MetricCard
        label="Candidates"
        note={`${formatNumber(candidates.total)} extracted candidates`}
        tone="coral"
        value={candidates.counts.pending || 0}
      />
      <MetricCard
        label="Faded memories"
        note={status.available ? "retained, excluded from normal recall" : "memory tables unavailable"}
        tone="yellow"
        value={knowledge.counts.faded || 0}
      />
    </div>
  );
}

function KnowledgeTable({ rows }: { rows: Knowledge[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Memory</th>
            <th>Status</th>
            <th>Kind</th>
            <th>Session</th>
            <th>Updated</th>
            <th>Preview</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.id}>
              <td className="primary">
                <strong>{item.title || "Untitled memory"}</strong>
                <small>{item.has_blob ? "blob stored" : "inline"} · {item.id.slice(0, 8)}…</small>
              </td>
              <td><StatusBadge value={item.memory_status} /></td>
              <td className="mono">{item.kind || "—"}</td>
              <td><SessionLink id={item.source_conversation_id} title={item.session_title} /></td>
              <td><DateCell value={item.updated_at} /></td>
              <td><ExpandableText value={item.body_preview} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CandidateTable({ rows }: { rows: MemoryCandidate[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Candidate</th>
            <th>Status</th>
            <th>Confidence</th>
            <th>Session</th>
            <th>Updated</th>
            <th>Summary</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((candidate) => (
            <tr key={candidate.id}>
              <td className="primary">
                <strong>{candidate.title}</strong>
                <small>{candidate.kind || "finding"} · {candidate.id.slice(0, 8)}…</small>
              </td>
              <td><StatusBadge value={candidate.status} /></td>
              <td className="mono">
                {candidate.confidence == null ? "—" : `${Math.round(candidate.confidence * 100)}%`}
              </td>
              <td><SessionLink id={candidate.conversation_id} title={candidate.session_title} /></td>
              <td><DateCell value={candidate.updated_at} /></td>
              <td><ExpandableText value={candidate.summary} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TaskTable({ rows }: { rows: MemoryTask[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Task</th>
            <th>Status</th>
            <th>Entity</th>
            <th>Attempts</th>
            <th>Updated</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((task) => (
            <tr key={task.id}>
              <td className="primary">
                <strong>{task.task_type || "task"}</strong>
                <small>{task.id.slice(0, 8)}…</small>
              </td>
              <td><StatusBadge value={task.status} /></td>
              <td className="mono">{task.entity_type || "—"} / {task.entity_id ? `${task.entity_id.slice(0, 8)}…` : "—"}</td>
              <td className="mono">{formatNumber(task.attempts)}</td>
              <td><DateCell value={task.updated_at} /></td>
              <td><ExpandableText value={task.last_error} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const emptyList = {
  data: [],
  pagination: { page: 1, limit: 25, total: 0, pages: 0, has_next: false, has_previous: false },
};

function defaultStatus(tab: MemoryTab): string {
  if (tab === "knowledge") return "active";
  if (tab === "tasks") return "pending";
  return "pending";
}

function statusOptions(tab: MemoryTab): Array<{ value: string; label: string }> {
  if (tab === "knowledge") {
    return [
      { value: "active", label: "active" },
      { value: "faded", label: "faded" },
      { value: "archived", label: "archived" },
      { value: "", label: "すべて" },
    ];
  }
  if (tab === "tasks") {
    return [
      { value: "pending", label: "pending" },
      { value: "running", label: "running" },
      { value: "done", label: "done" },
      { value: "error", label: "error" },
      { value: "", label: "すべて" },
    ];
  }
  return [
    { value: "pending", label: "pending" },
    { value: "promoted", label: "promoted" },
    { value: "dismissed", label: "dismissed" },
    { value: "expired", label: "expired" },
    { value: "", label: "すべて" },
  ];
}
