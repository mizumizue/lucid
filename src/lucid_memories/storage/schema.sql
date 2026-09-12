-- lucid-memories schema v14
-- turn_state: 直近のユーザー指示。hook が search/recall を先に走らせる。
-- retrieval_logs: 指示ごとの search/recall。digest が評価し、欠けは proposed link で埋める。
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  conversation_id TEXT PRIMARY KEY,
  parent_conversation_id TEXT,
  workspace_roots_json TEXT NOT NULL DEFAULT '[]',
  composer_mode TEXT,
  is_background INTEGER NOT NULL DEFAULT 0,
  model TEXT,
  transcript_path TEXT,
  title TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  last_generation_id TEXT,
  last_heartbeat_at TEXT,
  ended_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_status_heartbeat
  ON sessions(status, last_heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_sessions_parent
  ON sessions(parent_conversation_id);

-- The Cursor hook and the long-lived MCP server do not share a process
-- environment. This binding carries the most recent hook identity across
-- that boundary without guessing from an unrelated workspace session.
CREATE TABLE IF NOT EXISTS session_bindings (
  binding_key TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES sessions(conversation_id),
  workspace_root TEXT,
  bound_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_bindings_bound_at
  ON session_bindings(bound_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES sessions(conversation_id),
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  title TEXT,
  summary TEXT,
  subagent_id TEXT,
  subagent_type TEXT,
  tool_call_id TEXT,
  parent_job_id TEXT,
  claim_owner TEXT,
  claim_until TEXT,
  started_at TEXT,
  updated_at TEXT NOT NULL,
  ended_at TEXT,
  rev INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_jobs_conversation ON jobs(conversation_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_subagent
  ON jobs(subagent_id) WHERE subagent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS job_events (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  at TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  source TEXT NOT NULL,
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, at);

CREATE TABLE IF NOT EXISTS knowledge (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  scope TEXT NOT NULL,
  workspace_root TEXT,
  source_conversation_id TEXT,
  title TEXT NOT NULL,
  body TEXT,
  blob_sha TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL,
  importance REAL NOT NULL DEFAULT 0.5,
  salience REAL NOT NULL DEFAULT 0.5,
  decay_half_life_days REAL NOT NULL DEFAULT 90.0,
  access_count INTEGER NOT NULL DEFAULT 0,
  last_accessed_at TEXT,
  source_event_id TEXT,
  provenance_json TEXT NOT NULL DEFAULT '{}',
  memory_status TEXT NOT NULL DEFAULT 'active',
  expires_at TEXT,
  rev INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  created_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_knowledge_scope_ws ON knowledge(scope, workspace_root);
CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge(source_conversation_id);

CREATE TABLE IF NOT EXISTS packs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  conversation_id TEXT,
  workspace_root TEXT,
  title TEXT,
  token_estimate INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_packs_conv_kind ON packs(conversation_id, kind, created_at);

CREATE TABLE IF NOT EXISTS pack_items (
  id TEXT PRIMARY KEY,
  pack_id TEXT NOT NULL REFERENCES packs(id),
  ordinal INTEGER NOT NULL,
  knowledge_id TEXT,
  blob_sha TEXT,
  pointer_uri TEXT,
  load_priority INTEGER NOT NULL DEFAULT 100,
  token_estimate INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pack_items_pack
  ON pack_items(pack_id, load_priority, ordinal);

CREATE TABLE IF NOT EXISTS compact_events (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  generation_id TEXT,
  trigger TEXT,
  context_usage_percent REAL,
  context_tokens INTEGER,
  context_window_size INTEGER,
  message_count INTEGER,
  messages_to_compact INTEGER,
  is_first_compaction INTEGER,
  pack_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_compact_conv ON compact_events(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS blobs (
  sha256 TEXT PRIMARY KEY,
  bytes INTEGER NOT NULL,
  content_type TEXT,
  created_at TEXT NOT NULL,
  data BLOB
);

CREATE TABLE IF NOT EXISTS runtime_logs (
  id TEXT PRIMARY KEY,
  log_name TEXT NOT NULL,
  content BLOB NOT NULL,
  bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  content_type TEXT NOT NULL DEFAULT 'text/plain; charset=utf-8',
  created_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_runtime_logs_name
  ON runtime_logs(log_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_logs_created
  ON runtime_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS leases (
  resource_key TEXT PRIMARY KEY,
  owner_conversation_id TEXT NOT NULL,
  until TEXT NOT NULL,
  purpose TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notices (
  id TEXT PRIMARY KEY,
  to_conversation_id TEXT,
  from_conversation_id TEXT,
  workspace_root TEXT,
  kind TEXT NOT NULL,
  body TEXT,
  read_at TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notices_to ON notices(to_conversation_id, read_at, created_at);

CREATE TABLE IF NOT EXISTS ontology_nodes (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  normalized TEXT NOT NULL,
  pointer TEXT,
  knowledge_id TEXT,
  workspace_root TEXT,
  extra_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ontology_type_norm ON ontology_nodes(type, normalized);
CREATE INDEX IF NOT EXISTS idx_ontology_ws ON ontology_nodes(workspace_root);
CREATE INDEX IF NOT EXISTS idx_ontology_knowledge ON ontology_nodes(knowledge_id);
-- ontology_nodes_fts is created in lucid_memories/db.py (trigram, with tokenizer fallback).

CREATE TABLE IF NOT EXISTS turn_state (
  conversation_id TEXT PRIMARY KEY,
  last_prompt TEXT,
  last_prompt_at TEXT,
  workspace_root TEXT
);

CREATE TABLE IF NOT EXISTS retrieval_logs (
  id TEXT PRIMARY KEY,
  conversation_id TEXT,
  request_id TEXT,
  prompt TEXT NOT NULL,
  prompt_at TEXT NOT NULL,
  query TEXT,
  source TEXT NOT NULL,
  workspace_root TEXT,
  dbs_json TEXT NOT NULL DEFAULT '[]',
  links_json TEXT NOT NULL DEFAULT '[]',
  hits_json TEXT NOT NULL DEFAULT '[]',
  sqlite_hit_count INTEGER NOT NULL DEFAULT 0,
  map_hit_count INTEGER NOT NULL DEFAULT 0,
  link_count INTEGER NOT NULL DEFAULT 0,
  expected_json TEXT,
  matched_count INTEGER,
  expected_count INTEGER,
  gaps_json TEXT,
  improved_json TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_logs_at ON retrieval_logs(prompt_at DESC);
CREATE INDEX IF NOT EXISTS idx_retrieval_logs_source ON retrieval_logs(source, created_at DESC);

-- Normalized retrieval audit trail. Bodies remain in knowledge/Map; these
-- tables only record which request saw which result and why it ranked.
CREATE TABLE IF NOT EXISTS retrieval_requests (
  id TEXT PRIMARY KEY,
  conversation_id TEXT,
  generation_id TEXT,
  parent_request_id TEXT,
  method TEXT NOT NULL,
  query TEXT,
  target TEXT,
  workspace_root TEXT,
  source TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  started_at TEXT NOT NULL,
  completed_at TEXT,
  result_count INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_retrieval_requests_conversation
  ON retrieval_requests(conversation_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_retrieval_requests_generation
  ON retrieval_requests(generation_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_retrieval_requests_method
  ON retrieval_requests(method, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_retrieval_requests_workspace
  ON retrieval_requests(workspace_root, started_at DESC);

CREATE TABLE IF NOT EXISTS retrieval_results (
  id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES retrieval_requests(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  source_db TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT,
  title TEXT,
  rank INTEGER,
  score REAL,
  memory_score REAL,
  selected INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(request_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_results_request
  ON retrieval_results(request_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_retrieval_results_entity
  ON retrieval_results(source_db, entity_type, entity_id);

-- Dense vectors are stored as little-endian float32 BLOBs.  The embedding
-- provider/model is part of the key so a model change can coexist safely.
CREATE TABLE IF NOT EXISTS embeddings (
  id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  model TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  vector BLOB NOT NULL,
  text_hash TEXT NOT NULL,
  text_preview TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(entity_type, entity_id, model)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_entity
  ON embeddings(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_model
  ON embeddings(model, dimensions);

-- Generation-level usage is optional because older hooks do not send usage
-- data. Values are nullable so partial provider payloads remain useful.
CREATE TABLE IF NOT EXISTS usage_events (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  generation_id TEXT,
  event_type TEXT NOT NULL,
  model TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cache_read_tokens INTEGER,
  cache_write_tokens INTEGER,
  total_tokens INTEGER,
  context_tokens INTEGER,
  context_window_size INTEGER,
  cost_usd REAL,
  input_text TEXT,
  output_text TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_events_conversation
  ON usage_events(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_created
  ON usage_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_model
  ON usage_events(model, created_at DESC);

CREATE TABLE IF NOT EXISTS conversation_events (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  generation_id TEXT,
  event_type TEXT NOT NULL,
  role TEXT,
  tool_name TEXT,
  input_text TEXT,
  output_text TEXT,
  status TEXT,
  workspace_root TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_events_conversation
  ON conversation_events(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_events_generation
  ON conversation_events(generation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_events_type
  ON conversation_events(event_type, created_at DESC);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  event_id TEXT REFERENCES conversation_events(id),
  conversation_id TEXT NOT NULL,
  generation_id TEXT,
  workspace_root TEXT,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  relative_path TEXT,
  name TEXT,
  mime_type TEXT,
  size_bytes INTEGER,
  sha256 TEXT,
  blob_sha TEXT,
  content_preview TEXT,
  storage_scope TEXT NOT NULL DEFAULT 'workspace',
  storage_policy TEXT NOT NULL DEFAULT 'managed_elsewhere',
  source TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_conversation
  ON artifacts(conversation_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_path
  ON artifacts(path);
CREATE INDEX IF NOT EXISTS idx_artifacts_updated
  ON artifacts(updated_at DESC);

-- Durable background consolidation queue. Hooks only enqueue; a worker
-- performs bounded, retryable candidate extraction outside the hook path.
CREATE TABLE IF NOT EXISTS memory_tasks (
  id TEXT PRIMARY KEY,
  task_type TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  available_at TEXT NOT NULL,
  locked_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_tasks_ready
  ON memory_tasks(status, available_at, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_tasks_entity
  ON memory_tasks(task_type, entity_type, entity_id);

-- Candidate memories are intentionally separate from knowledge. A worker may
-- propose a memory without silently making it part of long-term context.
CREATE TABLE IF NOT EXISTS memory_candidates (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  conversation_id TEXT NOT NULL,
  workspace_root TEXT,
  kind TEXT NOT NULL DEFAULT 'finding',
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL,
  salience REAL NOT NULL DEFAULT 0.5,
  status TEXT NOT NULL DEFAULT 'pending',
  source_event_id TEXT NOT NULL,
  knowledge_id TEXT,
  expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_candidates_status
  ON memory_candidates(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_workspace
  ON memory_candidates(workspace_root, updated_at DESC);

-- Global persona learning is deliberately separate from generic memory.
-- Only repeated, explicit user preferences may be auto-applied.
CREATE TABLE IF NOT EXISTS persona_candidates (
  id TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  conversation_ids_json TEXT NOT NULL DEFAULT '[]',
  source_event_ids_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'pending',
  applied_revision_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_persona_candidates_status
  ON persona_candidates(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS persona_revisions (
  id TEXT PRIMARY KEY,
  candidate_id TEXT,
  action TEXT NOT NULL,
  before_hash TEXT,
  after_hash TEXT,
  before_json TEXT,
  after_json TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_persona_revisions_created
  ON persona_revisions(created_at DESC);
