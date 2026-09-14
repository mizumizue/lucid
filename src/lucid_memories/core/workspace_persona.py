"""Workspace-scoped persona bindings, overlays, and layered injection."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lucid_memories.core import persona
from lucid_memories.runtime.util import new_id, normalize_root
from lucid_memories.storage.db import connect, now_iso

TYPE_TEMPLATE_DIR = "types"
DEFAULT_TYPES = ("app-dev", "infra", "notes", "research", "meta")

DEFAULT_TYPE_TEMPLATES: dict[str, dict[str, Any]] = {
    "app-dev": {
        "type_id": "app-dev",
        "title": "Application development",
        "sections": [
            {
                "id": "scope",
                "title": "Scope",
                "content": "Prefer the smallest correct change. Match surrounding conventions.",
                "priority": 10,
            },
            {
                "id": "quality",
                "title": "Quality",
                "content": "Ship behavior the user can verify. Avoid speculative abstractions.",
                "priority": 20,
            },
        ],
    },
    "infra": {
        "type_id": "infra",
        "title": "Infrastructure",
        "sections": [
            {
                "id": "safety",
                "title": "Safety",
                "content": "Prefer idempotent, reversible changes. Call out blast radius before destructive steps.",
                "priority": 10,
            },
            {
                "id": "ops",
                "title": "Operations",
                "content": "Make observability and rollback paths explicit.",
                "priority": 20,
            },
        ],
    },
    "notes": {
        "type_id": "notes",
        "title": "Notes",
        "sections": [
            {
                "id": "brevity",
                "title": "Brevity",
                "content": "Optimize for readable prose and structure. Do not over-build software here.",
                "priority": 10,
            }
        ],
    },
    "research": {
        "type_id": "research",
        "title": "Research",
        "sections": [
            {
                "id": "evidence",
                "title": "Evidence",
                "content": "Separate facts from hypotheses. Prefer primary sources and note uncertainty.",
                "priority": 10,
            },
            {
                "id": "explore",
                "title": "Explore",
                "content": "Compare alternatives and trade-offs before recommending a path.",
                "priority": 20,
            },
        ],
    },
    "meta": {
        "type_id": "meta",
        "title": "Meta tooling",
        "sections": [
            {
                "id": "docs",
                "title": "Documentation",
                "content": "Follow the repository documentation workflow before non-trivial changes.",
                "priority": 10,
            },
            {
                "id": "validate",
                "title": "Validation",
                "content": "Run documentation validation when docs change.",
                "priority": 20,
            },
        ],
    },
}


def types_dir() -> Path:
    return persona.persona_dir() / TYPE_TEMPLATE_DIR


def ensure_type_templates() -> None:
    destination = types_dir()
    destination.mkdir(parents=True, exist_ok=True)
    for type_id, template in DEFAULT_TYPE_TEMPLATES.items():
        path = destination / f"{type_id}.json"
        if path.exists():
            continue
        payload = {
            "schema_version": 1,
            **template,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def list_type_ids() -> list[str]:
    ensure_type_templates()
    return sorted(
        path.stem
        for path in types_dir().glob("*.json")
        if path.is_file()
    )


def load_type_template(type_id: str) -> dict[str, Any]:
    ensure_type_templates()
    normalized = str(type_id or "").strip()
    if not normalized:
        raise ValueError("type_id is required")
    path = types_dir() / f"{normalized}.json"
    if not path.exists():
        raise KeyError(f"unknown workspace persona type: {normalized}")
    return _read_json(path)


def type_exists(type_id: str) -> bool:
    try:
        load_type_template(type_id)
        return True
    except (KeyError, ValueError, OSError, json.JSONDecodeError):
        return False


def _binding_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "workspace_root": row["workspace_root"],
        "type_id": row["type_id"],
        "status": row["status"],
        "rationale": row["rationale"],
        "confidence": row["confidence"],
        "proposed_by": row["proposed_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_binding(workspace: str | None) -> dict[str, Any] | None:
    root = normalize_root(workspace)
    if not root:
        return None
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM workspace_persona_bindings WHERE workspace_root = ?",
            (root,),
        ).fetchone()
        return _binding_row_to_dict(row) if row else None
    finally:
        conn.close()


def _record_binding_event(
    *,
    workspace_root: str,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    created_by: str,
) -> str:
    event_id = new_id()
    conn = connect()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO workspace_persona_binding_events(
                  id, workspace_root, action, before_json, after_json, created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    workspace_root,
                    action,
                    json.dumps(before, ensure_ascii=False) if before else None,
                    json.dumps(after, ensure_ascii=False) if after else None,
                    now_iso(),
                    created_by,
                ),
            )
    finally:
        conn.close()
    return event_id


def _upsert_binding(
    *,
    workspace: str,
    type_id: str | None,
    status: str,
    rationale: str | None = None,
    confidence: float | None = None,
    proposed_by: str = "agent",
    action: str,
    created_by: str,
) -> dict[str, Any]:
    root = normalize_root(workspace)
    if not root:
        raise ValueError("workspace is required")
    if type_id and not type_exists(type_id):
        raise KeyError(f"unknown workspace persona type: {type_id}")
    timestamp = now_iso()
    before = get_binding(root)
    conn = connect()
    try:
        with conn:
            if before:
                conn.execute(
                    """
                    UPDATE workspace_persona_bindings
                    SET type_id = ?, status = ?, rationale = ?, confidence = ?,
                        proposed_by = ?, updated_at = ?
                    WHERE workspace_root = ?
                    """,
                    (
                        type_id,
                        status,
                        rationale,
                        confidence,
                        proposed_by,
                        timestamp,
                        root,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO workspace_persona_bindings(
                      workspace_root, type_id, status, rationale, confidence,
                      proposed_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        root,
                        type_id,
                        status,
                        rationale,
                        confidence,
                        proposed_by,
                        timestamp,
                        timestamp,
                    ),
                )
    finally:
        conn.close()
    after = get_binding(root)
    event_id = _record_binding_event(
        workspace_root=root,
        action=action,
        before=before,
        after=after,
        created_by=created_by,
    )
    return {"ok": True, "binding": after, "event_id": event_id}


def propose_binding(
    *,
    workspace: str,
    type_id: str,
    rationale: str,
    confidence: float | None = None,
    proposed_by: str = "agent",
) -> dict[str, Any]:
    if confidence is not None:
        confidence = max(0.0, min(float(confidence), 1.0))
    return _upsert_binding(
        workspace=workspace,
        type_id=type_id,
        status="pending",
        rationale=rationale,
        confidence=confidence,
        proposed_by=proposed_by,
        action="propose",
        created_by=proposed_by,
    )


def confirm_binding(
    *,
    workspace: str,
    type_id: str | None = None,
    created_by: str = "user",
) -> dict[str, Any]:
    current = get_binding(workspace)
    if type_id:
        if not type_exists(type_id):
            return {"ok": False, "error": "invalid_type", "type_id": type_id}
        rationale = current["rationale"] if current else None
        confidence = current["confidence"] if current else None
        proposed_by = current["proposed_by"] if current else created_by
        return _upsert_binding(
            workspace=workspace,
            type_id=type_id,
            status="confirmed",
            rationale=rationale,
            confidence=confidence,
            proposed_by=proposed_by,
            action="confirm",
            created_by=created_by,
        )
    if not current or current["status"] != "pending" or not current.get("type_id"):
        return {"ok": False, "error": "not_pending", "workspace": normalize_root(workspace)}
    return _upsert_binding(
        workspace=workspace,
        type_id=current["type_id"],
        status="confirmed",
        rationale=current.get("rationale"),
        confidence=current.get("confidence"),
        proposed_by=current.get("proposed_by") or "agent",
        action="confirm",
        created_by=created_by,
    )


def reject_binding(*, workspace: str, created_by: str = "user") -> dict[str, Any]:
    current = get_binding(workspace)
    if not current:
        return {"ok": False, "error": "binding_not_found", "workspace": normalize_root(workspace)}
    root = normalize_root(workspace)
    conn = connect()
    try:
        with conn:
            conn.execute(
                "DELETE FROM workspace_persona_bindings WHERE workspace_root = ?",
                (root,),
            )
    finally:
        conn.close()
    event_id = _record_binding_event(
        workspace_root=root,
        action="reject",
        before=current,
        after=None,
        created_by=created_by,
    )
    return {"ok": True, "binding": None, "event_id": event_id}


def override_binding(
    *,
    workspace: str,
    type_id: str,
    created_by: str = "user",
) -> dict[str, Any]:
    if not type_exists(type_id):
        return {"ok": False, "error": "invalid_type", "type_id": type_id}
    current = get_binding(workspace)
    rationale = current.get("rationale") if current else "manual override"
    return _upsert_binding(
        workspace=workspace,
        type_id=type_id,
        status="confirmed",
        rationale=rationale,
        confidence=1.0,
        proposed_by=created_by,
        action="override",
        created_by=created_by,
    )


def load_workspace_overlay(workspace: str | None) -> dict[str, Any]:
    root = normalize_root(workspace)
    if not root:
        return {"sections": []}
    conn = connect()
    try:
        row = conn.execute(
            "SELECT sections_json FROM workspace_persona_overlays WHERE workspace_root = ?",
            (root,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"sections": []}
    sections = json.loads(row["sections_json"] or "[]")
    if not isinstance(sections, list):
        return {"sections": []}
    return {"sections": sections}


def set_workspace_overlay_section(
    *,
    workspace: str,
    section_id: str,
    title: str,
    content: str,
    priority: int = 80,
    created_by: str = "user",
) -> dict[str, Any]:
    root = normalize_root(workspace)
    if not root:
        raise ValueError("workspace is required")
    overlay = load_workspace_overlay(root)
    sections = [
        section
        for section in overlay.get("sections") or []
        if isinstance(section, dict) and str(section.get("id")) != section_id
    ]
    sections.append(
        {
            "id": section_id,
            "title": title,
            "content": persona._compact_text(content),
            "priority": priority,
        }
    )
    timestamp = now_iso()
    before = overlay
    after = {"sections": sections}
    conn = connect()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO workspace_persona_overlays(workspace_root, sections_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(workspace_root) DO UPDATE SET
                  sections_json = excluded.sections_json,
                  updated_at = excluded.updated_at
                """,
                (root, json.dumps(sections, ensure_ascii=False), timestamp),
            )
    finally:
        conn.close()
    event_id = _record_binding_event(
        workspace_root=root,
        action="overlay_set",
        before=before,
        after=after,
        created_by=created_by,
    )
    return {"ok": True, "overlay": after, "event_id": event_id}


def remove_workspace_overlay_section(
    *,
    workspace: str,
    section_id: str,
    created_by: str = "user",
) -> dict[str, Any]:
    root = normalize_root(workspace)
    overlay = load_workspace_overlay(root)
    before = overlay
    sections = [
        section
        for section in overlay.get("sections") or []
        if isinstance(section, dict) and str(section.get("id")) != section_id
    ]
    after = {"sections": sections}
    timestamp = now_iso()
    conn = connect()
    try:
        with conn:
            if sections:
                conn.execute(
                    """
                    INSERT INTO workspace_persona_overlays(workspace_root, sections_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(workspace_root) DO UPDATE SET
                      sections_json = excluded.sections_json,
                      updated_at = excluded.updated_at
                    """,
                    (root, json.dumps(sections, ensure_ascii=False), timestamp),
                )
            else:
                conn.execute(
                    "DELETE FROM workspace_persona_overlays WHERE workspace_root = ?",
                    (root,),
                )
    finally:
        conn.close()
    event_id = _record_binding_event(
        workspace_root=root,
        action="overlay_remove",
        before=before,
        after=after,
        created_by=created_by,
    )
    return {"ok": True, "overlay": after, "event_id": event_id}


def list_binding_events(*, workspace: str, limit: int = 20) -> list[dict[str, Any]]:
    root = normalize_root(workspace)
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT id, workspace_root, action, before_json, after_json, created_at, created_by
            FROM workspace_persona_binding_events
            WHERE workspace_root = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (root, max(1, min(limit, 200))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def infer_workspace_type(workspace: str | None) -> dict[str, Any]:
    root = normalize_root(workspace)
    path = Path(root) if root else None
    if not path or not path.is_dir():
        return {
            "ok": True,
            "type_id": "app-dev",
            "rationale": "Workspace path is missing or not a directory; defaulting to app-dev.",
            "confidence": 0.2,
            "markers": [],
        }

    markers: list[str] = []
    if (path / "src" / "lucid_memories").is_dir() or (path / "cli.py").is_file():
        markers.append("lucid-memories layout")
        return {
            "ok": True,
            "type_id": "meta",
            "rationale": "Repository matches lucid-memories tooling layout.",
            "confidence": 0.95,
            "markers": markers,
        }
    if (path / "docker-compose.yml").is_file() or (path / "docker-compose.yaml").is_file():
        markers.append("docker-compose")
    if (path / "terraform").is_dir() or any(path.glob("*.tf")):
        markers.append("terraform")
    if markers and ("terraform" in markers or "docker-compose" in markers):
        return {
            "ok": True,
            "type_id": "infra",
            "rationale": f"Infrastructure markers found: {', '.join(markers)}.",
            "confidence": 0.85,
            "markers": markers,
        }
    if (path / "package.json").is_file() or (path / "app.json").is_file() or (path / "pyproject.toml").is_file():
        markers.append("application manifest")
        return {
            "ok": True,
            "type_id": "app-dev",
            "rationale": f"Application project markers found: {', '.join(markers)}.",
            "confidence": 0.8,
            "markers": markers,
        }
    markdown_files = list(path.glob("**/*.md"))
    code_files = [
        *path.glob("**/*.py"),
        *path.glob("**/*.ts"),
        *path.glob("**/*.tsx"),
        *path.glob("**/*.js"),
    ]
    if len(markdown_files) >= 3 and len(code_files) <= 2:
        markers.append("markdown-heavy")
        return {
            "ok": True,
            "type_id": "notes",
            "rationale": "Workspace is markdown-heavy with little code.",
            "confidence": 0.7,
            "markers": markers,
        }
    if (path / "docs").is_dir() and (path / "research").is_dir():
        markers.extend(["docs/", "research/"])
        return {
            "ok": True,
            "type_id": "research",
            "rationale": "Documentation and research directories suggest exploratory work.",
            "confidence": 0.65,
            "markers": markers,
        }
    return {
        "ok": True,
        "type_id": "app-dev",
        "rationale": "No strong marker matched; defaulting to app-dev.",
        "confidence": 0.4,
        "markers": markers,
    }


def workspace_status(*, workspace: str) -> dict[str, Any]:
    root = normalize_root(workspace)
    binding = get_binding(root)
    overlay = load_workspace_overlay(root)
    return {
        "ok": True,
        "workspace": root,
        "binding": binding,
        "overlay": overlay,
        "available_types": list_type_ids(),
    }


def _sorted_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (section for section in sections if isinstance(section, dict)),
        key=lambda section: (int(section.get("priority", 100)), str(section.get("id", ""))),
    )


def compose_injection(
    workspace_root: str | None = None,
    *,
    token_budget: int | None = None,
) -> dict[str, Any]:
    budget = int(token_budget or persona.DEFAULT_TOKEN_BUDGET)
    global_persona = persona.load_persona()
    global_sections = _sorted_sections(global_persona.get("sections") or [])

    binding = get_binding(workspace_root) if workspace_root else None
    type_sections: list[dict[str, Any]] = []
    workspace_sections: list[dict[str, Any]] = []
    type_id: str | None = None
    if binding and binding.get("status") == "confirmed" and binding.get("type_id"):
        type_id = str(binding["type_id"])
        try:
            type_sections = _sorted_sections(load_type_template(type_id).get("sections") or [])
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            type_sections = []
        workspace_sections = _sorted_sections(load_workspace_overlay(workspace_root).get("sections") or [])

    header = (
        "[global persona]\n"
        "これはWorkspaceに依存しないユーザー方針です。"
        "内容を現在の応答と判断に常時適用してください。\n\n"
    )
    used = persona.estimate_tokens(header)
    included: list[str] = []
    omitted: list[str] = []
    layers = {"global": False, "type": False, "workspace": False}

    layer_blocks: list[tuple[str, str | None, list[dict[str, Any]]]] = [
        ("global", None, global_sections),
        ("type", f"[workspace persona: {type_id}]\n" if type_id else None, type_sections),
        (
            "workspace",
            "[workspace overlay]\n" if workspace_sections else None,
            workspace_sections,
        ),
    ]

    for layer_name, layer_header, sections in layer_blocks:
        if not sections and layer_name != "global":
            continue
        if layer_header:
            header_tokens = persona.estimate_tokens(layer_header)
            if used + header_tokens > budget:
                omitted.append(f"{layer_name}:__header__")
                for section in sections:
                    omitted.append(f"{layer_name}:{section.get('id') or section.get('title')}")
                continue
            included.append(layer_header.rstrip())
            used += header_tokens
            if layer_name != "global":
                layers[layer_name] = True
        elif layer_name == "global":
            layers["global"] = bool(global_sections)

        for section in sections:
            candidate = persona._section_text(section)
            candidate_tokens = persona.estimate_tokens(candidate)
            if used + candidate_tokens <= budget:
                included.append(candidate)
                used += candidate_tokens
                if layer_name == "global":
                    layers["global"] = True
                else:
                    layers[layer_name] = True
                continue
            omitted.append(f"{layer_name}:{section.get('id') or section.get('title')}")

    body = "\n\n".join(included)
    content = header + body if body else header.rstrip()
    return {
        "content": content,
        "token_estimate": persona.estimate_tokens(content),
        "token_budget": budget,
        "omitted_sections": omitted,
        "over_budget": bool(omitted),
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "layers": layers,
        "binding": binding,
    }
