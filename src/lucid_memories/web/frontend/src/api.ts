import type {
  Artifact,
  ConversationEvent,
  DailyResponse,
  Job,
  Knowledge,
  ListResponse,
  MapStatus,
  MemoryCandidate,
  MemoryStatus,
  MemoryTask,
  Overview,
  RecallGraphResponse,
  Session,
  SessionDetail,
} from "./types";

const API_ROOT = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly type?: string;

  constructor(message: string, status: number, type?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.type = type;
  }
}

type Query = Record<string, string | number | undefined>;

function queryString(query: Query): string {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    signal,
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const payload = (await response.json().catch(() => ({}))) as {
    detail?: string;
    type?: string;
  };
  if (!response.ok) {
    throw new ApiError(
      payload.detail || `API request failed (${response.status})`,
      response.status,
      payload.type,
    );
  }
  return payload as T;
}

export const api = {
  overview: (signal?: AbortSignal) => request<Overview>("/overview", signal),
  memoryStatus: (signal?: AbortSignal) => request<MemoryStatus>("/memory/status", signal),
  memoryCandidates: (query: Query, signal?: AbortSignal) =>
    request<ListResponse<MemoryCandidate>>(`/memory/candidates${queryString(query)}`, signal),
  memoryTasks: (query: Query, signal?: AbortSignal) =>
    request<ListResponse<MemoryTask>>(`/memory/tasks${queryString(query)}`, signal),
  knowledge: (query: Query, signal?: AbortSignal) =>
    request<ListResponse<Knowledge>>(`/knowledge${queryString(query)}`, signal),
  knowledgeItem: (id: string, signal?: AbortSignal) =>
    request<Knowledge>(`/knowledge/${encodeURIComponent(id)}`, signal),
  mapStatus: (signal?: AbortSignal) => request<MapStatus>("/map/status", signal),
  daily: (days: number, signal?: AbortSignal) =>
    request<DailyResponse>(`/analytics/daily${queryString({ days })}`, signal),
  recallGraph: (query: Query, signal?: AbortSignal) =>
    request<RecallGraphResponse>(`/recall-graph${queryString(query)}`, signal),
  sessions: (query: Query, signal?: AbortSignal) =>
    request<ListResponse<Session>>(`/sessions${queryString(query)}`, signal),
  session: (id: string, signal?: AbortSignal) =>
    request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`, signal),
  jobs: (query: Query, signal?: AbortSignal) =>
    request<ListResponse<Job>>(`/jobs${queryString(query)}`, signal),
  events: (query: Query, signal?: AbortSignal) =>
    request<ListResponse<ConversationEvent>>(`/events${queryString(query)}`, signal),
  artifacts: (query: Query, signal?: AbortSignal) =>
    request<ListResponse<Artifact>>(`/artifacts${queryString(query)}`, signal),
  artifact: (id: string, signal?: AbortSignal) =>
    request<Artifact>(`/artifacts/${encodeURIComponent(id)}`, signal),
};

