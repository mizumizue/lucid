from __future__ import annotations

import sqlite3

from lucid_memories.storage.paths import SUMMARY_LIMIT

SESSION_KIND_MAIN = "main"
SESSION_KIND_SUB = "sub"
SESSION_KIND_BACKGROUND = "background"

ORIGIN_HUMAN = "human"
ORIGIN_AGENT = "agent"
ORIGIN_UNKNOWN = "unknown"

HUMAN_EVENT_TYPES = frozenset({"beforeSubmitPrompt"})
AGENT_EVENT_TYPES = frozenset(
    {
        "postToolUse",
        "postToolUseFailure",
        "afterAgentResponse",
        "afterAgentThought",
        "afterShellExecution",
        "afterMCPExecution",
        "afterFileEdit",
        "subagentStart",
        "stop",
    }
)

FIRST_EVENT_SUBQUERY = """
(
  SELECT event_type
  FROM conversation_events ce
  WHERE ce.conversation_id = s.conversation_id
    AND ce.event_type NOT IN ('sessionEnd')
  ORDER BY ce.created_at ASC
  LIMIT 1
)
"""


def session_kind(session: dict) -> str:
    if session.get("parent_conversation_id"):
        return SESSION_KIND_SUB
    if session.get("is_background"):
        return SESSION_KIND_BACKGROUND
    return SESSION_KIND_MAIN


def session_origin(first_event_type: str | None) -> str:
    if not first_event_type:
        return ORIGIN_UNKNOWN
    if first_event_type in HUMAN_EVENT_TYPES:
        return ORIGIN_HUMAN
    if first_event_type in AGENT_EVENT_TYPES or first_event_type == "sessionStart":
        return ORIGIN_AGENT
    return ORIGIN_UNKNOWN


def build_brief(
    *,
    last_prompt: str | None,
    first_user_prompt: str | None,
    last_output: str | None,
) -> str | None:
    start = _first_line(first_user_prompt) or _first_line(last_prompt)
    if not start:
        return None
    outcome = _first_line(last_output)
    if outcome and outcome != start:
        return f"{_truncate(start, 120)} → {_truncate(outcome, 80)}"
    return _truncate(start, 160)


def compute_session_summary(
    connection: sqlite3.Connection,
    conversation_id: str,
    *,
    has_events: bool,
    has_jobs: bool,
    last_prompt: str | None = None,
    limit: int = SUMMARY_LIMIT,
) -> str | None:
    ids = [conversation_id]
    first_prompts = _fetch_first_user_prompts(connection, ids) if has_events else {}
    last_outputs = _fetch_last_outputs(connection, ids) if has_events else {}
    subagent_types = _fetch_subagent_types(connection, ids) if has_jobs else {}
    job_summaries = _fetch_job_summaries(connection, conversation_id) if has_jobs else []

    start = _first_line(first_prompts.get(conversation_id)) or _first_line(last_prompt)
    if not start:
        return None

    parts = [start]
    outcome = _first_line(last_outputs.get(conversation_id))
    if outcome and outcome != start:
        parts.append(f"→ {outcome}")

    sub_types = subagent_types.get(conversation_id, [])
    if sub_types:
        parts.append(f"[{', '.join(sub_types)}]")

    for summary in job_summaries[:2]:
        line = _first_line(summary)
        if line and line not in parts:
            parts.append(f"({line})")

    text = " ".join(parts).strip()
    return _truncate(text, limit) if text else None


def session_kind_sql_condition(kind: str) -> tuple[str, list[object]]:
    if kind == SESSION_KIND_MAIN:
        return (
            "COALESCE(s.parent_conversation_id, '') = '' AND COALESCE(s.is_background, 0) = 0",
            [],
        )
    if kind == SESSION_KIND_SUB:
        return ("COALESCE(s.parent_conversation_id, '') != ''", [])
    if kind == SESSION_KIND_BACKGROUND:
        return (
            "COALESCE(s.is_background, 0) != 0 AND COALESCE(s.parent_conversation_id, '') = ''",
            [],
        )
    return ("", [])


def session_origin_sql_condition(
    origin: str,
    *,
    has_events: bool,
) -> tuple[str, list[object]]:
    if not has_events or not origin:
        return ("", [])
    if origin == ORIGIN_HUMAN:
        placeholders = ",".join("?" * len(HUMAN_EVENT_TYPES))
        return (f"{FIRST_EVENT_SUBQUERY} IN ({placeholders})", list(HUMAN_EVENT_TYPES))
    if origin == ORIGIN_AGENT:
        agent_types = sorted(AGENT_EVENT_TYPES | {"sessionStart"})
        placeholders = ",".join("?" * len(agent_types))
        return (f"{FIRST_EVENT_SUBQUERY} IN ({placeholders})", agent_types)
    if origin == ORIGIN_UNKNOWN:
        known = sorted(HUMAN_EVENT_TYPES | AGENT_EVENT_TYPES | {"sessionStart"})
        placeholders = ",".join("?" * len(known))
        return (
            f"({FIRST_EVENT_SUBQUERY} IS NULL OR {FIRST_EVENT_SUBQUERY} NOT IN ({placeholders}))",
            known,
        )
    return ("", [])


def enrich_sessions(
    connection: sqlite3.Connection,
    sessions: list[dict],
    *,
    has_events: bool,
    has_jobs: bool,
) -> list[dict]:
    if not sessions:
        return sessions
    ids = [str(row["conversation_id"]) for row in sessions if row.get("conversation_id")]
    if not ids:
        return sessions

    first_events = _fetch_first_event_types(connection, ids) if has_events else {}
    first_prompts = _fetch_first_user_prompts(connection, ids) if has_events else {}
    last_outputs = _fetch_last_outputs(connection, ids) if has_events else {}
    subagent_types = _fetch_subagent_types(connection, ids) if has_jobs else {}
    parent_titles = _fetch_parent_titles(connection, sessions)

    enriched: list[dict] = []
    for session in sessions:
        cid = str(session.get("conversation_id") or "")
        kind = session_kind(session)
        origin = session_origin(first_events.get(cid))
        sub_types = subagent_types.get(cid, [])
        brief = build_brief(
            last_prompt=session.get("last_prompt"),
            first_user_prompt=first_prompts.get(cid),
            last_output=last_outputs.get(cid),
        )
        stored_summary = session.get("summary")
        parent_id = session.get("parent_conversation_id")
        item = dict(session)
        item.update(
            {
                "session_kind": kind,
                "origin": origin,
                "subagent_types": sub_types,
                "brief": stored_summary or brief,
                "parent_title": parent_titles.get(str(parent_id)) if parent_id else None,
            }
        )
        enriched.append(item)
    return enriched


def enrich_session_detail(
    connection: sqlite3.Connection,
    session: dict,
    *,
    has_events: bool,
    has_jobs: bool,
) -> dict:
    return enrich_sessions(
        connection,
        [session],
        has_events=has_events,
        has_jobs=has_jobs,
    )[0]


def _fetch_first_event_types(
    connection: sqlite3.Connection,
    conversation_ids: list[str],
) -> dict[str, str]:
    placeholders = ",".join("?" * len(conversation_ids))
    data = connection.execute(
        f"""
        SELECT conversation_id, event_type
        FROM conversation_events
        WHERE conversation_id IN ({placeholders})
          AND event_type NOT IN ('sessionEnd')
        ORDER BY created_at ASC
        """,
        conversation_ids,
    ).fetchall()
    first: dict[str, str] = {}
    for row in data:
        cid = str(row["conversation_id"])
        if cid not in first:
            first[cid] = str(row["event_type"])
    return first


def _fetch_first_user_prompts(
    connection: sqlite3.Connection,
    conversation_ids: list[str],
) -> dict[str, str]:
    placeholders = ",".join("?" * len(conversation_ids))
    data = connection.execute(
        f"""
        SELECT conversation_id, input_text
        FROM conversation_events
        WHERE conversation_id IN ({placeholders})
          AND event_type = 'beforeSubmitPrompt'
          AND COALESCE(input_text, '') != ''
        ORDER BY created_at ASC
        """,
        conversation_ids,
    ).fetchall()
    first: dict[str, str] = {}
    for row in data:
        cid = str(row["conversation_id"])
        if cid not in first and row["input_text"]:
            first[cid] = str(row["input_text"])
    return first


def _fetch_last_outputs(
    connection: sqlite3.Connection,
    conversation_ids: list[str],
) -> dict[str, str]:
    placeholders = ",".join("?" * len(conversation_ids))
    data = connection.execute(
        f"""
        SELECT conversation_id, output_text, event_type, role
        FROM conversation_events
        WHERE conversation_id IN ({placeholders})
          AND COALESCE(output_text, '') != ''
        ORDER BY created_at DESC
        """,
        conversation_ids,
    ).fetchall()
    last: dict[str, str] = {}
    for row in data:
        cid = str(row["conversation_id"])
        if cid in last:
            continue
        event_type = str(row["event_type"] or "")
        role = str(row["role"] or "")
        if role == "assistant" or event_type in {"afterAgentResponse", "stop"}:
            last[cid] = str(row["output_text"])
    return last


def _fetch_subagent_types(
    connection: sqlite3.Connection,
    conversation_ids: list[str],
) -> dict[str, list[str]]:
    placeholders = ",".join("?" * len(conversation_ids))
    data = connection.execute(
        f"""
        SELECT conversation_id, subagent_id, subagent_type
        FROM jobs
        WHERE kind = 'subagent'
          AND COALESCE(subagent_type, '') != ''
          AND (
            conversation_id IN ({placeholders})
            OR subagent_id IN ({placeholders})
          )
        ORDER BY updated_at DESC
        """,
        (*conversation_ids, *conversation_ids),
    ).fetchall()
    result: dict[str, set[str]] = {cid: set() for cid in conversation_ids}
    for row in data:
        subagent_type = str(row["subagent_type"])
        conversation_id = str(row["conversation_id"])
        subagent_id = str(row["subagent_id"] or "")
        if conversation_id in result:
            result[conversation_id].add(subagent_type)
        if subagent_id in result:
            result[subagent_id].add(subagent_type)
    return {cid: sorted(types) for cid, types in result.items() if types}


def _fetch_job_summaries(
    connection: sqlite3.Connection,
    conversation_id: str,
) -> list[str]:
    data = connection.execute(
        """
        SELECT COALESCE(summary, title) AS text
        FROM jobs
        WHERE conversation_id = ?
          AND COALESCE(summary, title, '') != ''
        ORDER BY updated_at DESC
        LIMIT 3
        """,
        (conversation_id,),
    ).fetchall()
    return [str(row["text"]) for row in data if row["text"]]


def _fetch_parent_titles(
    connection: sqlite3.Connection,
    sessions: list[dict],
) -> dict[str, str | None]:
    parent_ids = sorted(
        {
            str(session["parent_conversation_id"])
            for session in sessions
            if session.get("parent_conversation_id")
        }
    )
    if not parent_ids:
        return {}
    placeholders = ",".join("?" * len(parent_ids))
    data = connection.execute(
        f"""
        SELECT conversation_id, title
        FROM sessions
        WHERE conversation_id IN ({placeholders})
        """,
        parent_ids,
    ).fetchall()
    return {str(row["conversation_id"]): row["title"] for row in data}


def _first_line(value: str | None) -> str | None:
    if not value:
        return None
    line = value.splitlines()[0].strip()
    return line or None


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1]}…"
