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
  { id: "knowledge", label: "長期記憶 (Knowledge)" },
  { id: "candidates", label: "昇格候補 (Candidates)" },
  { id: "tasks", label: "統合タスク (Tasks)" },
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
        description="SQLite に保存された長期記憶（Knowledge）、昇格待ちの候補（Candidates）、統合タスク（Tasks）を読み取り専用で確認・検索します。"
        kicker="MEMORY LIFECYCLE"
        title="記憶ライフサイクル管理 (Memory Operations)"
      >
        <SearchInput
          label="記憶を検索"
          onChange={(value) => setValue("q", value)}
          placeholder="タイトル・本文・会話を検索..."
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
            状態
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
          title="長期記憶 (Knowledge)"
          resource={knowledge}
          empty="該当する記憶データ (Knowledge) は見つかりませんでした。"
          table={(rows: Knowledge[]) => <KnowledgeTable rows={rows} />}
          onPage={(next) => setValue("page", next, false)}
        />
      )}
      {tab === "candidates" && (
        <CollectionPanel
          action={candidates.data?.pagination.total}
          kicker="CANDIDATE QUEUE"
          title="昇格候補 (Memory Candidates)"
          resource={candidates}
          empty="該当する昇格候補 (Candidate) は見つかりませんでした。"
          table={(rows: MemoryCandidate[]) => <CandidateTable rows={rows} />}
          onPage={(next) => setValue("page", next, false)}
        />
      )}
      {tab === "tasks" && (
        <CollectionPanel
          action={tasks.data?.pagination.total}
          kicker="CONSOLIDATION QUEUE"
          title="統合タスク (Memory Tasks)"
          resource={tasks}
          empty="該当する統合タスク (Task) は見つかりませんでした。"
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
        label="有効な記憶 (Active)"
        note={`全 ${formatNumber(knowledge.total)} 件`}
        value={knowledge.counts.active || 0}
      />
      <MetricCard
        label="保留中の統合タスク"
        note={`統合処理待ち ${formatNumber(tasks.total)} 件`}
        tone="mint"
        value={tasks.counts.pending || 0}
      />
      <MetricCard
        label="抽出候補 (Candidates)"
        note={`昇格候補 ${formatNumber(candidates.total)} 件`}
        tone="coral"
        value={candidates.counts.pending || 0}
      />
      <MetricCard
        label="減衰した記憶 (Faded)"
        note={status.available ? "通常想起から除外・DB保持中" : "記憶テーブル未対応"}
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
            <th>記憶タイトル</th>
            <th>状態</th>
            <th>種別</th>
            <th>生成セッション</th>
            <th>最終更新</th>
            <th>本文プレビュー</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr key={item.id}>
              <td className="primary">
                <strong>{item.title || "無題の記憶"}</strong>
                <small>{item.has_blob ? "blob 永続化" : "インライン"} · {item.id.slice(0, 8)}…</small>
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
            <th>候補名</th>
            <th>状態</th>
            <th>確信度</th>
            <th>関連セッション</th>
            <th>最終更新</th>
            <th>要約プレビュー</th>
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
            <th>タスク種別</th>
            <th>状態</th>
            <th>対象エンティティ</th>
            <th>試行回数</th>
            <th>最終更新</th>
            <th>エラー詳細</th>
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
              <td><ExpandableText label="エラー詳細を表示" value={task.last_error} /></td>
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
