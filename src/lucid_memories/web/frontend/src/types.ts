export type Pagination = {
  page: number;
  limit: number;
  total: number;
  pages: number;
  has_next: boolean;
  has_previous: boolean;
};

export type ListResponse<T> = {
  data: T[];
  pagination: Pagination;
};

export type SessionKind = "main" | "sub" | "background";
export type SessionOrigin = "human" | "agent" | "unknown";

export type Session = {
  conversation_id: string;
  parent_conversation_id?: string | null;
  parent_title?: string | null;
  title?: string | null;
  summary?: string | null;
  status?: string | null;
  model?: string | null;
  composer_mode?: string | null;
  is_background?: number | boolean | null;
  last_prompt?: string | null;
  last_heartbeat_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  job_count?: number;
  compaction_count?: number;
  session_kind?: SessionKind | null;
  origin?: SessionOrigin | null;
  subagent_types?: string[] | null;
  brief?: string | null;
};

export type Job = {
  id: string;
  conversation_id?: string | null;
  kind?: string | null;
  status?: string | null;
  title?: string | null;
  summary?: string | null;
  subagent_type?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  ended_at?: string | null;
  session_title?: string | null;
  session_model?: string | null;
};

export type ConversationEvent = {
  id: string;
  conversation_id?: string | null;
  event_type?: string | null;
  role?: string | null;
  tool_name?: string | null;
  input_text?: string | null;
  output_text?: string | null;
  status?: string | null;
  created_at?: string | null;
  session_title?: string | null;
};

export type Artifact = {
  id: string;
  conversation_id?: string | null;
  path?: string | null;
  relative_path?: string | null;
  name?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  sha256?: string | null;
  blob_sha?: string | null;
  storage_scope?: string | null;
  storage_policy?: string | null;
  content_preview?: string | null;
  content_availability?: "blob" | "external" | "preview" | "unavailable" | string | null;
  origin?: string | null;
  blob_recorded?: boolean;
  path_exists?: boolean;
  preview?: string | null;
  message?: string | null;
  kind?: string | null;
  source?: string | null;
  workspace_root?: string | null;
  updated_at?: string | null;
  session_title?: string | null;
};

export type Usage = {
  id?: string;
  created_at?: string | null;
  model?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  cost_usd?: number | null;
};

export type McpUsage = {
  id?: string;
  created_at?: string | null;
  generation_id?: string | null;
  tool_name?: string | null;
  tokens?: number | null;
  total_tokens?: number | null;
  budget?: number | null;
  tokens_used?: number | null;
  is_large?: boolean | null;
};

export type McpTokenStats = {
  sample_size: number;
  mean?: number | null;
  median?: number | null;
  p75?: number | null;
  p90?: number | null;
  p95?: number | null;
  max?: number | null;
  large_threshold?: number | null;
  large_label?: string | null;
};

export type McpSummary = {
  status: "available" | "no_events" | "unavailable" | string;
  available: boolean;
  events: number;
  tokens: number;
  by_tool: Array<{ tool: string; events: number; tokens: number; stats?: McpTokenStats }>;
  stats: McpTokenStats;
  message: string;
};

export type Compaction = {
  id?: string;
  created_at?: string | null;
  context_tokens?: number | null;
  context_usage_percent?: number | null;
  messages_to_compact?: number | null;
};

export type Overview = {
  database: string;
  schema_version?: number | null;
  database_updated_at?: string | null;
  counts: {
    sessions: number;
    live_sessions: number;
    jobs: number;
    open_jobs: number;
    knowledge: number;
    packs: number;
    compactions: number;
    retrievals: number;
  };
  models: Array<{ model: string; sessions: number }>;
  recent_sessions: Session[];
  usage: {
    status: "available" | "no_events" | "unavailable";
    available: boolean;
    events: number;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    cost_usd: number;
    message: string;
  };
  mcp: McpSummary;
  index: {
    available: boolean;
    events: number;
    artifacts: number;
  };
  memory?: MemoryStatus;
  embeddings?: EmbeddingStatus;
  map?: MapStatus;
  storage?: StorageSummary;
  retrieval?: RetrievalSummary;
  hooks?: HookCoverage;
  persona?: PersonaStatus;
  context?: ContextSummary;
  capabilities: Record<string, boolean>;
};

export type StatusGroup = {
  available: boolean;
  total: number;
  counts: Record<string, number>;
};

export type MemoryStatus = {
  available: boolean;
  knowledge: StatusGroup;
  tasks: StatusGroup;
  candidates: StatusGroup;
};

export type EmbeddingModel = {
  model: string;
  dimensions: number;
  embeddings: number;
  vector_bytes: number;
  latest_at?: string | null;
};

export type EmbeddingStatus = {
  available: boolean;
  total: number;
  vector_bytes?: number;
  models: EmbeddingModel[];
};

export type MapStatus = {
  available: boolean;
  nodes: number;
  by_type: Record<string, number>;
  graph_available: boolean;
  relation_counts: Record<string, number>;
  message?: string;
};

export type StorageSummary = {
  blobs: { available: boolean; count: number; bytes: number };
  artifact_scopes: Array<{ scope: string; artifacts: number; bytes: number }>;
};

export type RetrievalSummary = {
  available: boolean;
  events: number;
  with_hits: number;
  hit_rate: number | null;
};

export type HookCoverage = {
  available: boolean;
  status: "available" | "partial" | "no_events" | "unavailable" | string;
  events: number;
  by_type: Record<string, number>;
  missing: string[];
  expected?: string[];
  message?: string;
};

export type PersonaStatus = {
  available: boolean;
  path_present: boolean;
  scope?: string | null;
  sections: number;
  token_estimate?: number | null;
  token_budget?: number | null;
  updated_at?: string | null;
  candidates: StatusGroup;
  revisions: number;
  message?: string;
};

export type ContextSummary = {
  available: boolean;
  packs: number;
  token_estimate: number;
  items: number;
  message?: string;
};

export type DailyPoint = {
  day: string;
  sessions: number;
  jobs: number;
  completed_jobs: number;
  compactions: number;
  context_tokens: number;
  context_usage_percent?: number | null;
  retrievals: number;
  packs: number;
  usage_events: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_usd: number;
  mcp_calls: number;
  mcp_tokens: number;
};

export type DailyResponse = {
  data: DailyPoint[];
  meta: { days: number };
};

export type RecallGraphNode = {
  id: string;
  type: "conversation" | "retrieval" | "memory" | "map" | string;
  label?: string | null;
  database?: string | null;
  kind?: string | null;
  conversation_id?: string | null;
  request_id?: string | null;
  prompt?: string | null;
  body_preview?: string | null;
  retrieval_count?: number | null;
  last_recalled_at?: string | null;
  temperature?: number | null;
  [key: string]: unknown;
};

export type RecallGraphEdge = {
  id: string;
  source: string;
  target: string;
  relation: string;
  count?: number | null;
  rank?: number | null;
  score?: number | null;
  memory_score?: number | null;
  selected?: boolean | null;
  [key: string]: unknown;
};

export type RecallGraphResponse = {
  ok: boolean;
  nodes: RecallGraphNode[];
  edges: RecallGraphEdge[];
  meta: {
    workspace?: string | null;
    conversation_id?: string | null;
    since?: string | null;
    until?: string | null;
    limit: number;
    log_count: number;
    generated_at: string;
  };
};

export type MemoryCandidate = {
  id: string;
  event_id: string;
  conversation_id: string;
  workspace_root?: string | null;
  kind?: string | null;
  title: string;
  summary: string;
  tags_json?: string | null;
  confidence?: number | null;
  salience?: number | null;
  status?: string | null;
  source_event_id?: string | null;
  knowledge_id?: string | null;
  expires_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  session_title?: string | null;
};

export type Knowledge = {
  id: string;
  kind?: string | null;
  title?: string | null;
  body?: string | null;
  body_preview?: string | null;
  body_truncated?: boolean;
  blob_sha?: string | null;
  has_blob?: boolean;
  memory_status?: string | null;
  source_conversation_id?: string | null;
  source_event_id?: string | null;
  confidence?: number | null;
  importance?: number | null;
  salience?: number | null;
  access_count?: number | null;
  last_accessed_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  session_title?: string | null;
};

export type MemoryTask = {
  id: string;
  task_type?: string | null;
  entity_type?: string | null;
  entity_id?: string | null;
  status?: string | null;
  attempts?: number | null;
  available_at?: string | null;
  locked_at?: string | null;
  last_error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type SessionDetail = {
  data: Session;
  relationships: {
    jobs: Job[];
    events: ConversationEvent[];
    usage: Usage[];
    mcp: McpUsage[];
    artifacts: Artifact[];
    compactions: Compaction[];
  };
  input_output: {
    input?: string | null;
    output?: string | null;
    message: string;
  };
};

