"""Global, workspace-independent persona storage and rendering."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lucid_memories.storage.db import connect, now_iso
from lucid_memories.storage.paths import bus_home
from lucid_memories.runtime.util import dumps, new_id, truncate


DEFAULT_TOKEN_BUDGET = 8_000
PERSONA_DIR = "persona"
USER_RULES_FILENAME = "user-rules.json"
PERSONA_FILENAME = "persona.json"
SCHEMA_VERSION = 1

_PREFERENCE_MARKERS = re.compile(
    r"(?:常に|毎回|いつも|今後|これから|以後|覚えて|記憶して|"
    r"好み|してほしい|して欲しい|"
    r"(?:日本語|英語|簡潔|短く|詳しく|丁寧).{0,20}(?:答えて|説明して|にして)|"
    r"always|from now on|remember|prefer|please always)",
    re.IGNORECASE,
)
_LOCAL_MARKERS = re.compile(
    r"(?:今回だけ|この(?:workspace|ワークスペース|repo|リポジトリ|ファイル|作業)|"
    r"この(?:会話|セッション)|for this (?:workspace|file|task)|only this time)",
    re.IGNORECASE,
)


def persona_dir() -> Path:
    return bus_home() / PERSONA_DIR


def user_rules_path() -> Path:
    return persona_dir() / USER_RULES_FILENAME


def persona_path() -> Path:
    return persona_dir() / PERSONA_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"persona JSON のルートは object である必要があります: {path}")
    return value


def estimate_tokens(text: str) -> int:
    """Conservative, dependency-free token estimate for budget enforcement."""
    ascii_chars = sum(character.isascii() for character in text)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, math.ceil(ascii_chars / 4) + non_ascii_chars)


def _compact_text(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _rule_sections(source: dict[str, Any]) -> list[dict[str, Any]]:
    rules = source.get("rules")
    if not isinstance(rules, list):
        raise ValueError("user-rules.json の rules は配列である必要があります")
    sections: list[dict[str, Any]] = []
    for ordinal, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"UserRule #{ordinal + 1} は object である必要があります")
        title = str(rule.get("title") or f"UserRule {ordinal + 1}").strip()
        content = _compact_text(str(rule.get("content") or ""))
        if not content:
            continue
        sections.append(
            {
                "id": str(rule.get("id") or f"rule-{ordinal + 1}"),
                "title": title,
                "content": content,
                "priority": ordinal,
                "source_id": rule.get("id"),
            }
        )
    return sections


def build_persona(
    source: dict[str, Any],
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> dict[str, Any]:
    if token_budget < 1:
        raise ValueError("token_budget は1以上である必要があります")
    sections = _rule_sections(source)
    result = {
        "schema_version": SCHEMA_VERSION,
        "scope": "user",
        "workspace_root": None,
        "token_budget": token_budget,
        "source": str(source.get("source") or "cursor-user-rules"),
        "updated_at": _now(),
        "sections": sections,
    }
    rendered = render_persona(result)
    result["token_estimate"] = rendered["token_estimate"]
    result["content_hash"] = rendered["content_hash"]
    return result


def _section_text(section: dict[str, Any]) -> str:
    return f"## {section['title']}\n{section['content']}"


def render_persona(
    persona: dict[str, Any],
    *,
    token_budget: int | None = None,
) -> dict[str, Any]:
    budget = int(token_budget or persona.get("token_budget") or DEFAULT_TOKEN_BUDGET)
    if budget < 1:
        raise ValueError("token_budget は1以上である必要があります")
    raw_sections = persona.get("sections")
    if not isinstance(raw_sections, list):
        raise ValueError("persona.json の sections は配列である必要があります")

    sections = sorted(
        (section for section in raw_sections if isinstance(section, dict)),
        key=lambda section: (int(section.get("priority", 100)), str(section.get("id", ""))),
    )
    header = (
        "[global persona]\n"
        "これはWorkspaceに依存しないユーザー方針です。"
        "内容を現在の応答と判断に常時適用してください。\n\n"
    )
    included: list[str] = []
    omitted: list[str] = []
    used = estimate_tokens(header)
    for section in sections:
        title = str(section.get("title") or section.get("id") or "Persona").strip()
        content = _compact_text(str(section.get("content") or ""))
        if not content:
            continue
        candidate = _section_text({"title": title, "content": content})
        candidate_tokens = estimate_tokens(candidate)
        if used + candidate_tokens <= budget:
            included.append(candidate)
            used += candidate_tokens
            continue
        omitted.append(str(section.get("id") or title))

    body = "\n\n".join(included)
    content = header + body if body else header.rstrip()
    return {
        "content": content,
        "token_estimate": estimate_tokens(content),
        "token_budget": budget,
        "omitted_sections": omitted,
        "over_budget": bool(omitted),
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def save_persona(persona: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    destination = path or persona_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(persona, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return persona


def export_user_rules(
    source_path: Path | None = None,
    destination: Path | None = None,
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> dict[str, Any]:
    source = _read_json(source_path or user_rules_path())
    persona = build_persona(source, token_budget=token_budget)
    return save_persona(persona, destination)


def load_persona(path: Path | None = None) -> dict[str, Any]:
    destination = path or persona_path()
    if destination.exists():
        return _read_json(destination)
    source = _read_json(user_rules_path())
    return build_persona(source)


def get_injection(
    path: Path | None = None,
    *,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    if workspace_root:
        from lucid_memories.core import workspace_persona

        return workspace_persona.compose_injection(workspace_root)
    persona_doc = load_persona(path)
    rendered = render_persona(persona_doc)
    return {
        "content": rendered["content"],
        "token_estimate": rendered["token_estimate"],
        "token_budget": rendered["token_budget"],
        "omitted_sections": rendered["omitted_sections"],
        "over_budget": rendered["over_budget"],
        "content_hash": rendered["content_hash"],
    }


def validate_persona(path: Path | None = None) -> dict[str, Any]:
    destination = path or persona_path()
    try:
        persona = load_persona(destination)
        rendered = render_persona(persona)
        return {
            "ok": True,
            "path": str(destination),
            "scope": persona.get("scope"),
            "workspace_root": persona.get("workspace_root"),
            "sections": len(persona.get("sections") or []),
            **rendered,
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "path": str(destination), "error": str(exc)}


def update_section(
    section_id: str,
    *,
    title: str | None = None,
    content: str | None = None,
    priority: int | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    destination = path or persona_path()
    persona = load_persona(destination)
    sections = persona.get("sections")
    if not isinstance(sections, list):
        raise ValueError("persona.json の sections は配列である必要があります")
    for section in sections:
        if isinstance(section, dict) and str(section.get("id")) == section_id:
            if title is not None:
                section["title"] = title
            if content is not None:
                section["content"] = _compact_text(content)
            if priority is not None:
                section["priority"] = priority
            persona["updated_at"] = _now()
            rendered = render_persona(persona)
            persona["token_estimate"] = rendered["token_estimate"]
            persona["content_hash"] = rendered["content_hash"]
            return save_persona(persona, destination)
    raise KeyError(f"persona section が見つかりません: {section_id}")


def set_section(
    *,
    section_id: str,
    title: str,
    content: str,
    priority: int = 100,
    path: Path | None = None,
) -> dict[str, Any]:
    destination = path or persona_path()
    try:
        persona = load_persona(destination)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        persona = {
            "schema_version": SCHEMA_VERSION,
            "scope": "user",
            "workspace_root": None,
            "token_budget": DEFAULT_TOKEN_BUDGET,
            "source": "lucid-memories",
            "sections": [],
        }
    sections = persona.setdefault("sections", [])
    if not isinstance(sections, list):
        raise ValueError("persona.json の sections は配列である必要があります")
    sections.append(
        {
            "id": section_id,
            "title": title,
            "content": _compact_text(content),
            "priority": priority,
        }
    )
    persona["updated_at"] = _now()
    rendered = render_persona(persona)
    persona["token_estimate"] = rendered["token_estimate"]
    persona["content_hash"] = rendered["content_hash"]
    return save_persona(persona, destination)


def _normalize_preference(text: str) -> str:
    value = _compact_text(text).strip("「」\"' ")
    value = re.sub(r"^(?:今後は|これからは|以後は|常に|毎回)\s*", "", value)
    value = re.sub(r"[。.!！?？]+$", "", value).strip()
    return value


def detect_preference(prompt: str) -> dict[str, str] | None:
    """Extract only explicit, global-looking user preferences."""
    source = _compact_text(prompt)
    if len(source) < 8 or _LOCAL_MARKERS.search(source):
        return None
    sentences = [
        part.strip()
        for part in re.split(r"(?:\n|[。！？!?])+", source)
        if part.strip()
    ]
    candidates = [sentence for sentence in sentences if _PREFERENCE_MARKERS.search(sentence)]
    if not candidates:
        return None
    body = _normalize_preference(candidates[0])
    if len(body) < 8 or len(body) > 280:
        return None
    fingerprint_text = re.sub(r"[\s、,。.!！?？]+", "", body).lower()
    return {
        "fingerprint": hashlib.sha256(fingerprint_text.encode("utf-8")).hexdigest(),
        "title": truncate(f"User preference: {body}", 160) or "User preference",
        "body": body,
    }


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed if item]


def record_preference_candidate(
    prompt: str,
    *,
    conversation_id: str,
    source_event_id: str,
) -> dict[str, Any]:
    detected = detect_preference(prompt)
    if not detected or not conversation_id or not source_event_id:
        return {"ok": True, "candidate": False}
    timestamp = now_iso()
    conn = connect()
    try:
        with conn:
            row = conn.execute(
                "SELECT * FROM persona_candidates WHERE fingerprint = ?",
                (detected["fingerprint"],),
            ).fetchone()
            if row:
                conversation_ids = _json_list(row["conversation_ids_json"])
                source_event_ids = _json_list(row["source_event_ids_json"])
                if conversation_id not in conversation_ids:
                    conversation_ids.append(conversation_id)
                if source_event_id not in source_event_ids:
                    source_event_ids.append(source_event_id)
                conn.execute(
                    """
                    UPDATE persona_candidates
                    SET evidence_count = ?, conversation_ids_json = ?,
                        source_event_ids_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        len(conversation_ids),
                        dumps(conversation_ids),
                        dumps(source_event_ids),
                        timestamp,
                        row["id"],
                    ),
                )
                candidate_id = row["id"]
            else:
                candidate_id = new_id()
                conn.execute(
                    """
                    INSERT INTO persona_candidates(
                      id, fingerprint, title, body, evidence_count,
                      conversation_ids_json, source_event_ids_json,
                      status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        candidate_id,
                        detected["fingerprint"],
                        detected["title"],
                        detected["body"],
                        dumps([conversation_id]),
                        dumps([source_event_id]),
                        timestamp,
                        timestamp,
                    ),
                )
            candidate = conn.execute(
                "SELECT * FROM persona_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            return {"ok": True, "candidate": dict(candidate) if candidate else None}
    finally:
        conn.close()


def learn_from_events(*, limit: int = 20) -> dict[str, Any]:
    """Collect bounded preference candidates from user prompt events."""
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT id, conversation_id, input_text
            FROM conversation_events
            WHERE event_type = 'beforeSubmitPrompt'
              AND role = 'user'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    finally:
        conn.close()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        result = record_preference_candidate(
            row["input_text"] or "",
            conversation_id=row["conversation_id"],
            source_event_id=row["id"],
        )
        if result.get("candidate"):
            candidates.append(result["candidate"])
    return {"ok": True, "processed": len(rows), "candidates": candidates}


def _preference_topics(text: str) -> set[str]:
    topics: set[str] = set()
    if re.search(r"(?:日本語|英語|language|言語)", text, re.IGNORECASE):
        topics.add("language")
    if re.search(r"(?:簡潔|短く|冗長|詳しく|詳細|brevity|concise|verbose)", text, re.IGNORECASE):
        topics.add("brevity")
    if re.search(r"(?:説明|解説|コード|エラー|explain)", text, re.IGNORECASE):
        topics.add("explanation")
    if re.search(r"(?:丁寧|敬語|口調|tone|polite)", text, re.IGNORECASE):
        topics.add("tone")
    return topics


def _same_preference_topic(left: str, right: str) -> str | None:
    left_topics = _preference_topics(left)
    right_topics = _preference_topics(right)
    shared = left_topics & right_topics
    return sorted(shared)[0] if shared else None


def _preference_relation(candidate: str, existing: str) -> str | None:
    candidate_key = re.sub(r"[\s、,。.!！?？]+", "", candidate).lower()
    existing_key = re.sub(r"[\s、,。.!！?？]+", "", existing).lower()
    if candidate_key == existing_key:
        return "duplicate"
    topic = _same_preference_topic(candidate, existing)
    if topic is None:
        return None
    if topic == "language":
        candidate_language = "日本語" if "日本語" in candidate else "英語" if "英語" in candidate else ""
        existing_language = "日本語" if "日本語" in existing else "英語" if "英語" in existing else ""
        return "duplicate" if candidate_language == existing_language else "conflict"
    if topic == "brevity":
        candidate_short = bool(re.search(r"(?:簡潔|短く|concise)", candidate, re.IGNORECASE))
        existing_short = bool(re.search(r"(?:簡潔|短く|concise)", existing, re.IGNORECASE))
        return "duplicate" if candidate_short == existing_short else "conflict"
    return "conflict"


def _persona_hash(persona: dict[str, Any]) -> str:
    return render_persona(persona)["content_hash"]


def _update_candidate_status(
    candidate_id: str,
    *,
    status: str,
    revision_id: str | None = None,
) -> None:
    conn = connect()
    try:
        with conn:
            conn.execute(
                """
                UPDATE persona_candidates
                SET status = ?, applied_revision_id = COALESCE(?, applied_revision_id),
                    updated_at = ?
                WHERE id = ?
                """,
                (status, revision_id, now_iso(), candidate_id),
            )
    finally:
        conn.close()


def apply_ready_candidates(
    *,
    min_conversations: int = 2,
    limit: int = 5,
) -> dict[str, Any]:
    """Apply repeated, non-conflicting preferences to the global persona."""
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM persona_candidates
            WHERE status = 'pending' AND evidence_count >= ?
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (max(2, min_conversations), max(1, min(limit, 20))),
        ).fetchall()
    finally:
        conn.close()

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        candidate = dict(row)
        current = load_persona()
        relation = None
        for section in current.get("sections") or []:
            relation = _preference_relation(
                candidate["body"],
                str(section.get("content") or ""),
            )
            if relation:
                break
        if relation:
            status = f"skipped_{relation}"
            _update_candidate_status(candidate["id"], status=status)
            skipped.append({"id": candidate["id"], "status": status})
            continue

        before_json = json.dumps(current, ensure_ascii=False, sort_keys=True)
        before_hash = _persona_hash(current)
        updated = dict(current)
        sections = list(updated.get("sections") or [])
        sections.append(
            {
                "id": f"auto-{candidate['id'][:12]}",
                "title": candidate["title"],
                "content": candidate["body"],
                "priority": 50,
                "source": "auto-preference",
                "candidate_id": candidate["id"],
            }
        )
        updated["sections"] = sections
        updated["updated_at"] = now_iso()
        rendered = render_persona(updated)
        if rendered["omitted_sections"]:
            _update_candidate_status(candidate["id"], status="skipped_budget")
            skipped.append({"id": candidate["id"], "status": "skipped_budget"})
            continue
        updated["token_estimate"] = rendered["token_estimate"]
        updated["content_hash"] = rendered["content_hash"]
        after_json = json.dumps(updated, ensure_ascii=False, sort_keys=True)
        revision_id = new_id()
        save_persona(updated)
        revision_conn = connect()
        try:
            with revision_conn:
                revision_conn.execute(
                    """
                    INSERT INTO persona_revisions(
                      id, candidate_id, action, before_hash, after_hash,
                      before_json, after_json, created_at, created_by
                    ) VALUES (?, ?, 'apply', ?, ?, ?, ?, ?, 'auto-preference')
                    """,
                    (
                        revision_id,
                        candidate["id"],
                        before_hash,
                        rendered["content_hash"],
                        before_json,
                        after_json,
                        now_iso(),
                    ),
                )
                revision_conn.execute(
                    """
                    UPDATE persona_candidates
                    SET status = 'applied', applied_revision_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (revision_id, now_iso(), candidate["id"]),
                )
        finally:
            revision_conn.close()
        applied.append({"id": candidate["id"], "revision_id": revision_id})
    return {"ok": True, "applied": applied, "skipped": skipped}


def persona_worker(*, limit: int = 20, auto_apply: bool = True) -> dict[str, Any]:
    learned = learn_from_events(limit=limit)
    applied = apply_ready_candidates(limit=max(1, min(limit, 5))) if auto_apply else {
        "ok": True,
        "applied": [],
        "skipped": [],
    }
    return {"ok": True, "learned": learned, "applied": applied}


def list_preference_candidates(*, status: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM persona_candidates
            WHERE (? = '' OR status = ?)
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (status, status, max(1, min(limit, 200))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_persona_revisions(*, limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT id, candidate_id, action, before_hash, after_hash, created_at, created_by
            FROM persona_revisions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def reject_preference_candidate(candidate_id: str) -> dict[str, Any]:
    if not candidate_id:
        return {"ok": False, "error": "candidate_id required"}
    _update_candidate_status(candidate_id, status="rejected")
    return {"ok": True, "candidate_id": candidate_id, "status": "rejected"}


def rollback_persona_revision(revision_id: str) -> dict[str, Any]:
    conn = connect()
    try:
        revision = conn.execute(
            "SELECT * FROM persona_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
    finally:
        conn.close()
    if revision is None:
        return {"ok": False, "error": "revision_not_found", "id": revision_id}
    current = load_persona()
    if _persona_hash(current) != revision["after_hash"]:
        return {"ok": False, "error": "revision_conflict", "id": revision_id}
    restored = json.loads(revision["before_json"])
    before_json = json.dumps(current, ensure_ascii=False, sort_keys=True)
    after_json = json.dumps(restored, ensure_ascii=False, sort_keys=True)
    save_persona(restored)
    rollback_id = new_id()
    rollback_conn = connect()
    try:
        with rollback_conn:
            rollback_conn.execute(
                """
                INSERT INTO persona_revisions(
                  id, candidate_id, action, before_hash, after_hash,
                  before_json, after_json, created_at, created_by
                ) VALUES (?, ?, 'rollback', ?, ?, ?, ?, ?, 'manual')
                """,
                (
                    rollback_id,
                    revision["candidate_id"],
                    _persona_hash(current),
                    _persona_hash(restored),
                    before_json,
                    after_json,
                    now_iso(),
                ),
            )
    finally:
        rollback_conn.close()
    return {"ok": True, "revision_id": revision_id, "rollback_id": rollback_id}
