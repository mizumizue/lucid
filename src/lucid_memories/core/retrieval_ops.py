from __future__ import annotations

import json
import os
from typing import Any

from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.runtime.util import dumps, new_id, normalize_root, query_tokens
from . import retrieval
from . import api_common
from .embedding_ops import semantic_search
from .knowledge_ops import search

_STOP_WORDS = {
    "how",
    "should",
    "the",
    "a",
    "an",
    "to",
    "of",
    "and",
    "or",
    "for",
    "with",
    "this",
    "that",
    "what",
    "when",
    "where",
    "why",
    "is",
    "are",
    "was",
    "be",
    "do",
    "does",
    "can",
    "could",
    "would",
    "i",
    "we",
    "you",
    "it",
    "vs",
    "from",
}

DEFAULT_EVAL_CASES = [
    {
        "prompt": "指示パイプラインで検索と提案書き込みをして",
        "expect": [
            {"db": "map", "kind": "topic", "title": "指示パイプライン"},
            {"db": "sqlite", "title": "指示パイプラインは Auto、確定だけ Ask"},
        ],
    },
    {
        "prompt": "lucid-memories の文脈でパイプラインを引け",
        "expect": [
            {"db": "map", "kind": "context", "title": "lucid-memories"},
        ],
    },
]

def _prompt_query(prompt: str, max_chars: int = 180) -> str:
    tokens: list[str] = []
    for token in query_tokens(prompt):
        if token.lower() in _STOP_WORDS:
            continue
        tokens.append(token)
        if len(" ".join(tokens)) >= max_chars:
            break
    if tokens:
        return " ".join(tokens)[:max_chars]
    return " ".join((prompt or "").split())[:max_chars]

def _search_hits_for_prompt(
    prompt: str,
    workspace: str | None,
    *,
    conversation_id: str | None = None,
    generation_id: str | None = None,
) -> list[dict[str, Any]]:
    q = _prompt_query(prompt)
    tokens = [t for t in q.split() if len(t) >= 3][:6]
    queries = [q] + [t for t in tokens if t != q]
    seen: set[str] = set()
    hits: list[dict[str, Any]] = []
    semantic = semantic_search(
        prompt,
        workspace=workspace,
        limit=4,
        conversation_id=conversation_id,
        generation_id=generation_id,
        track=False,
    )
    for item in semantic.get("knowledge") or []:
        kid = str(item.get("id") or "")
        if not kid or kid in seen:
            continue
        seen.add(kid)
        hits.append(item)
        if len(hits) >= 4:
            return hits
    for query in queries:
        if not query:
            continue
        found = search(
            query,
            workspace=workspace,
            limit=4,
            conversation_id=conversation_id,
            generation_id=generation_id,
            track=False,
        )
        for item in found.get("knowledge") or []:
            kid = str(item.get("id") or "")
            if not kid or kid in seen:
                continue
            seen.add(kid)
            hits.append(item)
            if len(hits) >= 4:
                return hits
    return hits

def _flatten_retrieval(
    recall: dict[str, Any],
    knowledge: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    hits: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    dbs: list[str] = []
    seen_hits: set[str] = set()
    seen_links: set[str] = set()

    def add_hit(db: str, kind: str, item_id: Any, title: Any, extra: dict[str, Any] | None = None) -> None:
        key = f"{db}:{item_id or title}"
        if not item_id and not title:
            return
        if key in seen_hits:
            return
        seen_hits.add(key)
        rec = {"db": db, "kind": kind, "id": item_id, "title": title}
        if extra:
            rec.update(extra)
        hits.append(rec)
        if db not in dbs:
            dbs.append(db)

    def add_link(
        rel: str,
        to_db: str,
        to_kind: str,
        to_id: Any,
        to_title: Any,
        *,
        status: Any = None,
        from_id: Any = None,
        from_title: Any = None,
    ) -> None:
        key = f"{rel}:{from_id}:{to_id or to_title}"
        if key in seen_links:
            return
        seen_links.add(key)
        links.append(
            {
                "rel": rel,
                "from_id": from_id,
                "from_title": from_title,
                "to_db": to_db,
                "to_kind": to_kind,
                "to_id": to_id,
                "to_title": to_title,
                "status": status,
            }
        )

    for rank, item in enumerate(knowledge, start=1):
        add_hit(
            "sqlite",
            str(item.get("kind") or "knowledge"),
            item.get("id"),
            item.get("title"),
            {
                "rank": item.get("rank", rank),
                "score": item.get("score"),
                "memory_score": item.get("memory_score"),
            },
        )
    for topic in recall.get("topics") or []:
        add_hit("map", "topic", topic.get("id"), topic.get("title"), {"sense": topic.get("sense")})
        add_link("ABOUT", "map", "topic", topic.get("id"), topic.get("title"), status=topic.get("status"))
    for ctx in recall.get("contexts") or []:
        add_hit("map", "context", ctx.get("id"), ctx.get("title"), {"sense": ctx.get("sense")})
        add_link("IN_CONTEXT", "map", "context", ctx.get("id"), ctx.get("title"), status=ctx.get("status"))
    for proc in recall.get("procedures") or []:
        add_hit(
            "map",
            "procedure",
            proc.get("id"),
            proc.get("title"),
            {"pointer": proc.get("pointer")},
        )
        via = proc.get("triggers") or {}
        add_link(
            str(via.get("rel") or "TRIGGERS"),
            "map",
            "procedure",
            proc.get("id"),
            proc.get("title"),
            status=via.get("status"),
            from_id=via.get("directive_id"),
            from_title=via.get("directive_title"),
        )
        for mat in proc.get("materials") or []:
            db = "sqlite" if mat.get("knowledge_id") else "map"
            add_hit(db, "material", mat.get("knowledge_id") or mat.get("id"), mat.get("title"))
            add_link(
                "USES",
                db,
                "material",
                mat.get("knowledge_id") or mat.get("id"),
                mat.get("title"),
                from_id=proc.get("id"),
                from_title=proc.get("title"),
            )
    return hits, links, dbs

def collect_retrieval(
    prompt: str,
    workspace: str | None = None,
    *,
    conversation_id: str | None = None,
    generation_id: str | None = None,
    source: str = "api",
    request_id: str | None = None,
    track: bool = True,
) -> dict[str, Any]:
    q = _prompt_query(prompt)
    tracking_id = request_id
    if track and tracking_id is None:
        tracking_id = retrieval.start_request(
            "collect_retrieval",
            query=q,
            workspace=workspace,
            conversation_id=conversation_id,
            generation_id=generation_id,
            source=source,
        )
    recall: dict[str, Any] = {"ok": True, "procedures": [], "topics": [], "contexts": []}
    knowledge: list[dict[str, Any]] = []
    if q:
        try:
            from . import graph

            recall = graph.recall(
                q,
                workspace=workspace,
                budget_tokens=400,
                conversation_id=conversation_id,
                generation_id=generation_id,
                track=False,
            )
        except Exception as exc:
            recall = {"ok": False, "error": str(exc), "procedures": [], "topics": [], "contexts": []}
        try:
            knowledge = _search_hits_for_prompt(
                prompt,
                workspace,
                conversation_id=conversation_id,
                generation_id=generation_id,
            )
        except Exception:
            knowledge = []
    hits, links, dbs = _flatten_retrieval(recall, knowledge)
    if tracking_id:
        try:
            retrieval.record_results(
                tracking_id,
                retrieval.normalize_results(hits),
            )
            retrieval.finish_request(tracking_id)
        except Exception:
            retrieval.fail_request(tracking_id)
            raise
    return {
        "query": q,
        "recall": recall,
        "knowledge": knowledge,
        "hits": hits,
        "links": links,
        "dbs": dbs,
        "retrieval_request_id": tracking_id,
    }

def log_retrieval(
    prompt: str,
    *,
    conversation_id: str | None = None,
    prompt_at: str | None = None,
    query: str | None = None,
    source: str = "digest",
    workspace: str | None = None,
    hits: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    dbs: list[str] | None = None,
    expected: list[dict[str, Any]] | None = None,
    matched_count: int | None = None,
    expected_count: int | None = None,
    generation_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    text = (prompt or "").strip()
    if not text:
        return {"ok": False, "error": "prompt required"}
    hit_list = hits or []
    link_list = links or []
    db_list = dbs or sorted({str(h.get("db")) for h in hit_list if h.get("db")})
    sqlite_n = sum(1 for h in hit_list if h.get("db") == "sqlite")
    map_n = sum(1 for h in hit_list if h.get("db") == "map")
    now = now_iso()
    tracking_id = request_id or retrieval.start_request(
        "log_retrieval",
        query=query,
        workspace=workspace,
        conversation_id=conversation_id,
        generation_id=generation_id,
        source=source,
        at=prompt_at or now,
    )
    retrieval.record_results(
        tracking_id,
        retrieval.normalize_results(hit_list),
        at=prompt_at or now,
    )
    retrieval.finish_request(tracking_id, at=prompt_at or now)
    log_id = new_id()
    conn = api_common._conn()
    try:
        with write_tx(conn):
            conn.execute(
                """
                INSERT INTO retrieval_logs(
                  id, conversation_id, request_id, prompt, prompt_at, query, source, workspace_root,
                  dbs_json, links_json, hits_json, sqlite_hit_count, map_hit_count,
                  link_count, expected_json, matched_count, expected_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    conversation_id,
                    tracking_id,
                    text,
                    prompt_at or now,
                    query,
                    source,
                    normalize_root(workspace) if workspace else None,
                    dumps(db_list),
                    dumps(link_list),
                    dumps(hit_list),
                    sqlite_n,
                    map_n,
                    len(link_list),
                    dumps(expected) if expected is not None else None,
                    matched_count,
                    expected_count,
                    now,
                ),
            )
        return {
            "ok": True,
            "id": log_id,
            "request_id": tracking_id,
            "sqlite_hit_count": sqlite_n,
            "map_hit_count": map_n,
            "link_count": len(link_list),
            "dbs": db_list,
        }
    finally:
        conn.close()

def retrieval_gaps(
    hits: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> list[str]:
    has_sqlite = any(h.get("db") == "sqlite" for h in hits)
    has_map = any(h.get("db") == "map" for h in hits)
    has_proc = any(h.get("kind") == "procedure" for h in hits)
    has_topic = any(h.get("kind") == "topic" for h in hits)
    has_ctx = any(h.get("kind") == "context" for h in hits)
    has_uses = any(str(link.get("rel") or "").upper() == "USES" for link in links)
    gaps: list[str] = []
    if not hits:
        gaps.append("empty")
        return gaps
    if has_sqlite and not has_map:
        gaps.append("sqlite_only")
    if (has_topic or has_ctx) and not has_proc:
        gaps.append("map_without_procedure")
    if has_proc and has_sqlite and not has_uses:
        gaps.append("procedure_without_material")
    return gaps

def _patch_retrieval_eval(
    log_id: str,
    gaps: list[str],
    improved: list[dict[str, Any]],
) -> None:
    conn = api_common._conn()
    try:
        with write_tx(conn):
            conn.execute(
                "UPDATE retrieval_logs SET gaps_json = ?, improved_json = ? WHERE id = ?",
                (dumps(gaps), dumps(improved), log_id),
            )
    finally:
        conn.close()

def improve_retrieval(
    prompt: str,
    collected: dict[str, Any],
    *,
    workspace: str | None = None,
    conversation_id: str | None = None,
    log_id: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    hits = list(collected.get("hits") or [])
    links = list(collected.get("links") or [])
    gaps = retrieval_gaps(hits, links)
    proposed: list[dict[str, Any]] = []
    if not apply or not gaps or gaps == ["empty"]:
        if log_id:
            _patch_retrieval_eval(log_id, gaps, proposed)
        return {"ok": True, "gaps": gaps, "proposed": proposed}
    topics = [h for h in hits if h.get("kind") == "topic" and h.get("title")]
    contexts = [h for h in hits if h.get("kind") == "context" and h.get("title")]
    procs = [h for h in hits if h.get("kind") == "procedure" and h.get("title")]
    dirs = [h for h in hits if h.get("kind") == "directive" and h.get("title")]
    sqlite_hits = [h for h in hits if h.get("db") == "sqlite" and h.get("id")]
    needles = query_tokens(prompt) + [str(t.get("title") or "") for t in topics]

    def _score(item: dict[str, Any]) -> int:
        title = str(item.get("title") or "").lower()
        return sum(1 for needle in needles if needle and needle.lower() in title)

    sqlite_hits = [item for item in sqlite_hits if _score(item) > 0]
    sqlite_hits.sort(key=_score, reverse=True)
    from . import graph

    def _same_name(left: str, right: str) -> bool:
        return " ".join(left.strip().lower().split()) == " ".join(right.strip().lower().split())

    def _propose(**kwargs: Any) -> None:
        if len(proposed) >= 4:
            return
        kwargs.setdefault("workspace", workspace)
        kwargs.setdefault("conversation_id", conversation_id)
        kwargs.setdefault("source", "auto")
        kwargs.setdefault("label", "eval improve")
        result = graph.link(**kwargs)
        proposed.append(
            {
                "rel": kwargs.get("rel"),
                "from": kwargs.get("from_ref"),
                "to": kwargs.get("to_ref"),
                "ok": bool(result.get("ok")),
                "status": (result.get("edge") or {}).get("status"),
                "error": result.get("error"),
            }
        )

    about_sources = [(d, "directive") for d in dirs[:2]] + [(p, "procedure") for p in procs[:2]]
    for src, src_type in about_sources:
        src_title = str(src.get("title") or "")
        if not src_title:
            continue
        for topic in topics[:2]:
            title = str(topic.get("title") or "")
            if title:
                _propose(
                    from_ref=src_title,
                    to_ref=title,
                    rel="ABOUT",
                    from_type=src_type,
                    to_type="topic",
                    sense="topic",
                )
        for ctx in contexts[:1]:
            title = str(ctx.get("title") or "")
            if title:
                _propose(
                    from_ref=src_title,
                    to_ref=title,
                    rel="IN_CONTEXT",
                    from_type=src_type,
                    to_type="context",
                    sense="context",
                )
    if "map_without_procedure" in gaps:
        for directive in dirs[:2]:
            d_title = str(directive.get("title") or "")
            for proc in procs[:2]:
                p_title = str(proc.get("title") or "")
                if not d_title or not p_title or _same_name(d_title, p_title):
                    continue
                _propose(
                    from_ref=d_title,
                    to_ref=p_title,
                    rel="TRIGGERS",
                    from_type="directive",
                    to_type="procedure",
                    sense="related",
                )
    if "map_without_procedure" in gaps or "procedure_without_material" in gaps:
        for proc in procs[:2]:
            p_title = str(proc.get("title") or "")
            if not p_title:
                continue
            for item in sqlite_hits[:2]:
                title = str(item.get("title") or "")
                if not title:
                    continue
                _propose(
                    from_ref=p_title,
                    to_ref=title,
                    rel="USES",
                    from_type="procedure",
                    to_type="material",
                    knowledge_id=str(item.get("id")),
                    sense="related",
                )
    if log_id:
        _patch_retrieval_eval(log_id, gaps, proposed)
    return {"ok": True, "gaps": gaps, "proposed": proposed}

def improve_from_logs(
    limit: int = 20,
    *,
    workspace: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    conn = api_common._conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM retrieval_logs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    results: list[dict[str, Any]] = []
    for row in rows:
        hits = json.loads(row["hits_json"] or "[]")
        links = json.loads(row["links_json"] or "[]")
        gaps = retrieval_gaps(hits, links)
        already = row["improved_json"] if "improved_json" in row.keys() else None
        if already:
            continue
        if not gaps or gaps == ["empty"]:
            continue
        collected = {
            "query": row["query"],
            "hits": hits,
            "links": links,
        }
        results.append(
            improve_retrieval(
                row["prompt"],
                collected,
                workspace=workspace or row["workspace_root"],
                conversation_id=conversation_id or row["conversation_id"],
                log_id=row["id"],
                apply=True,
            )
        )
    return {"ok": True, "n": len(results), "results": results, "summary": measure_retrieval(limit)}

def _match_expected(hits: list[dict[str, Any]], expected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    used: set[int] = set()
    for exp in expected:
        exp_id = str(exp.get("id") or "")
        exp_title = str(exp.get("title") or "").strip().lower()
        exp_db = str(exp.get("db") or "")
        exp_kind = str(exp.get("kind") or "")
        for idx, hit in enumerate(hits):
            if idx in used:
                continue
            if exp_db and hit.get("db") != exp_db:
                continue
            if exp_kind and str(hit.get("kind") or "") != exp_kind:
                continue
            hit_id = str(hit.get("id") or "")
            hit_title = str(hit.get("title") or "").strip().lower()
            id_ok = bool(exp_id) and hit_id == exp_id
            title_ok = bool(exp_title) and (hit_title == exp_title or exp_title in hit_title or hit_title in exp_title)
            if id_ok or title_ok:
                used.add(idx)
                matched.append(hit)
                break
    return matched

def _rate(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round(num / den, 4)

def measure_retrieval(limit: int = 50) -> dict[str, Any]:
    conn = api_common._conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM retrieval_logs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        n = len(rows)
        any_hit = 0
        sqlite_hit = 0
        map_hit = 0
        linked = 0
        eval_n = 0
        eval_matched = 0
        eval_expected = 0
        gapped = 0
        recent: list[dict[str, Any]] = []
        for row in rows:
            sqlite_n = int(row["sqlite_hit_count"] or 0)
            map_n = int(row["map_hit_count"] or 0)
            if sqlite_n + map_n > 0:
                any_hit += 1
            if sqlite_n > 0:
                sqlite_hit += 1
            if map_n > 0:
                map_hit += 1
            if int(row["link_count"] or 0) > 0:
                linked += 1
            if row["expected_count"] is not None:
                eval_n += 1
                eval_matched += int(row["matched_count"] or 0)
                eval_expected += int(row["expected_count"] or 0)
            keys = row.keys()
            gaps = json.loads(row["gaps_json"] or "[]") if "gaps_json" in keys and row["gaps_json"] else []
            if gaps:
                gapped += 1
            recent.append(
                {
                    "id": row["id"],
                    "prompt": row["prompt"],
                    "prompt_at": row["prompt_at"],
                    "source": row["source"],
                    "dbs": json.loads(row["dbs_json"] or "[]"),
                    "sqlite_hit_count": sqlite_n,
                    "map_hit_count": map_n,
                    "link_count": int(row["link_count"] or 0),
                    "matched_count": row["matched_count"],
                    "expected_count": row["expected_count"],
                    "gaps": gaps,
                }
            )
        return {
            "ok": True,
            "n": n,
            "hit_rate": _rate(any_hit, n),
            "empty_rate": _rate(n - any_hit, n),
            "sqlite_hit_rate": _rate(sqlite_hit, n),
            "map_hit_rate": _rate(map_hit, n),
            "link_rate": _rate(linked, n),
            "gap_rate": _rate(gapped, n),
            "eval_n": eval_n,
            "eval_recall": _rate(eval_matched, eval_expected),
            "recent": recent,
        }
    finally:
        conn.close()

def eval_retrieval(
    cases: list[dict[str, Any]],
    *,
    workspace: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    if not cases:
        return {"ok": False, "error": "cases required"}
    results: list[dict[str, Any]] = []
    matched_all = 0
    expected_all = 0
    any_hit = 0
    for case in cases:
        prompt = str(case.get("prompt") or "").strip()
        expected = list(case.get("expect") or [])
        collected = collect_retrieval(
            prompt,
            workspace,
            conversation_id=conversation_id,
            source="eval",
        )
        matched = _match_expected(collected["hits"], expected)
        expected_all += len(expected)
        matched_all += len(matched)
        if collected["hits"]:
            any_hit += 1
        logged = log_retrieval(
            prompt,
            conversation_id=conversation_id,
            query=collected["query"],
            source="eval",
            workspace=workspace,
            hits=collected["hits"],
            links=collected["links"],
            dbs=collected["dbs"],
            expected=expected,
            matched_count=len(matched),
            expected_count=len(expected),
            request_id=collected.get("retrieval_request_id"),
        )
        results.append(
            {
                "prompt": prompt,
                "ok": logged.get("ok"),
                "dbs": collected["dbs"],
                "hits": collected["hits"],
                "links": collected["links"],
                "matched_count": len(matched),
                "expected_count": len(expected),
            }
        )
    n = len(cases)
    return {
        "ok": True,
        "n": n,
        "hit_rate": _rate(any_hit, n),
        "eval_recall": _rate(matched_all, expected_all),
        "matched": matched_all,
        "expected": expected_all,
        "cases": results,
        "summary": measure_retrieval(),
    }

def _digest_memory_lines(
    prompt: str,
    workspace: str | None,
    *,
    conversation_id: str | None = None,
    prompt_at: str | None = None,
    generation_id: str | None = None,
) -> list[str]:
    collected = collect_retrieval(
        prompt,
        workspace,
        conversation_id=conversation_id,
        generation_id=generation_id,
        source="digest",
    )
    q = collected["query"]
    if not q:
        return []
    try:
        logged = log_retrieval(
            prompt,
            conversation_id=conversation_id,
            prompt_at=prompt_at,
            query=q,
            source="digest",
            workspace=workspace,
            hits=collected["hits"],
            links=collected["links"],
            dbs=collected["dbs"],
            generation_id=generation_id,
            request_id=collected.get("retrieval_request_id"),
        )
    except Exception:
        logged = {}
    try:
        improved = improve_retrieval(
            prompt,
            collected,
            workspace=workspace,
            conversation_id=conversation_id,
            log_id=logged.get("id"),
            apply=False,
        )
    except Exception as exc:
        improved = {"gaps": [], "proposed": [], "error": str(exc)}
    lines: list[str] = []
    rec = collected["recall"]
    procs = rec.get("procedures") or []
    topics = rec.get("topics") or []
    ctxs = rec.get("contexts") or []
    if rec.get("ok") is False:
        lines.append("recall: (skipped)")
    elif procs or topics or ctxs:
        lines.append("recall:")
        for proc in procs[:4]:
            title = proc.get("title") or proc.get("id")
            lines.append(f"  Procedure {title}")
        for topic in topics[:2]:
            lines.append(f"  Topic {topic.get('title') or topic.get('id')}")
        for ctx in ctxs[:2]:
            lines.append(f"  Context {ctx.get('title') or ctx.get('id')}")
    else:
        lines.append("recall: (none)")
    items = collected["knowledge"]
    if items:
        lines.append("search:")
        for item in items[:3]:
            lines.append(f"  {item.get('kind')} {item.get('id')} {item.get('title')}")
    else:
        lines.append("search: (none)")
    if improved.get("error"):
        lines.append("eval: error=" + str(improved.get("error")))
    else:
        gaps = improved.get("gaps") or []
        lines.append("eval: gaps=" + (",".join(gaps) if gaps else "none"))
    proposed = [p for p in (improved.get("proposed") or []) if p.get("ok")]
    if proposed:
        bits = [f"{p.get('rel')} {p.get('from')} -> {p.get('to')}" for p in proposed[:4]]
        lines.append("improve: proposed " + "; ".join(bits))
    lines.append(
        "Auto: persist proposed remember/link this turn; ask the user only for confirm/forbid/archive."
    )
    return lines
