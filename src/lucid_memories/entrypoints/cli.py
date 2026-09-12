#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lucid_memories.core import api
from lucid_memories.core import graph
from lucid_memories.core import persona
from lucid_memories.storage.paths import DEFAULT_LOAD_BUDGET


def _print(data: Any) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")


def _tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def main(argv: list[str] | None = None) -> int:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--session", dest="conversation_id", default=None)
    shared.add_argument("--generation-id", dest="generation_id", default=None)
    shared.add_argument("--workspace", default=None)

    parser = argparse.ArgumentParser(prog="lucid-memories", description="Cursor agent shared memories")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", parents=[shared])
    sub.add_parser("status", parents=[shared])

    p_dashboard = sub.add_parser(
        "dashboard",
        parents=[shared],
        help="serve the local dashboard",
    )
    p_dashboard.add_argument(
        "action",
        nargs="?",
        default=None,
        choices=["open", "start"],
        help="optional action (e.g. 'open' to launch browser)",
    )
    p_dashboard.add_argument("--host", default="127.0.0.1")
    p_dashboard.add_argument("--port", type=int, default=8765)
    p_dashboard.add_argument("--open", dest="open_browser", action="store_true")

    p_open = sub.add_parser(
        "open",
        parents=[shared],
        help="serve the local dashboard and open in browser",
    )
    p_open.add_argument("--host", default="127.0.0.1")
    p_open.add_argument("--port", type=int, default=8765)

    p_search = sub.add_parser("search", parents=[shared])
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=20)

    p_vsearch = sub.add_parser("vsearch", parents=[shared])
    p_vsearch.add_argument("query")
    p_vsearch.add_argument("--limit", type=int, default=8)
    p_vsearch.add_argument("--min-score", type=float, default=0.20)
    p_vsearch.add_argument("--kind", default=None)

    p_search.add_argument("--kind", default=None)

    p_list = sub.add_parser("list", parents=[shared])
    p_list.add_argument("--kind", default=None)
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--all", dest="any_workspace", action="store_true")

    p_load = sub.add_parser("load", parents=[shared])
    p_load.add_argument("target")
    p_load.add_argument("--budget", type=int, default=DEFAULT_LOAD_BUDGET)

    p_remember = sub.add_parser("remember", parents=[shared])
    p_remember.add_argument("--title", default=None)
    p_remember.add_argument("--body", default=None)
    p_remember.add_argument("--body-file", dest="body_file", default=None)
    p_remember.add_argument("--kind", default=None)
    p_remember.add_argument("--scope", default="workspace")
    p_remember.add_argument("--tags", default=None)
    p_remember.add_argument("--confidence", type=float, default=None)
    p_remember.add_argument("--importance", type=float, default=None)
    p_remember.add_argument("--salience", type=float, default=None)
    p_remember.add_argument("--decay-half-life-days", type=float, default=None)
    p_remember.add_argument("--expires-at", default=None)
    p_remember.add_argument("--source-event-id", default=None)
    p_remember.add_argument("--provenance", default=None)
    p_remember.add_argument("--id", dest="knowledge_id", default=None)
    p_remember.add_argument("--rev", type=int, default=None)
    p_remember.add_argument("--created-at", dest="created_at", default=None)

    p_reload = sub.add_parser("reload", parents=[shared])
    p_reload.add_argument("--budget", type=int, default=DEFAULT_LOAD_BUDGET)

    p_relay = sub.add_parser("relay")
    relay_sub = p_relay.add_subparsers(dest="relay_cmd", required=True)
    p_rs = relay_sub.add_parser("save", parents=[shared])
    p_rs.add_argument("--title", required=True)
    p_rs.add_argument("--body", default=None)
    p_rs.add_argument("--body-file", dest="body_file", default=None)
    p_rs.add_argument("--skills", default="")
    p_rs.add_argument("--focus", default=None)
    p_rs.add_argument("--scope", default="workspace")
    p_rl = relay_sub.add_parser("load", parents=[shared])
    p_rl.add_argument("--id", dest="pack_id", default=None)
    p_rl.add_argument("--query", default=None)
    p_rl.add_argument("--budget", type=int, default=DEFAULT_LOAD_BUDGET)

    p_job = sub.add_parser("job")
    job_sub = p_job.add_subparsers(dest="job_cmd", required=True)
    p_js = job_sub.add_parser("start", parents=[shared])
    p_js.add_argument("--title", required=True)
    p_js.add_argument("--kind", default="declared")
    p_js.add_argument("--summary", default=None)
    p_ju = job_sub.add_parser("update", parents=[shared])
    p_ju.add_argument("--id", required=True)
    p_ju.add_argument("--rev", type=int, required=True)
    p_ju.add_argument("--status", default=None)
    p_ju.add_argument("--summary", default=None)
    p_ju.add_argument("--title", default=None)
    p_jd = job_sub.add_parser("done", parents=[shared])
    p_jd.add_argument("--id", required=True)
    p_jd.add_argument("--rev", type=int, required=True)
    p_jd.add_argument("--summary", default=None)
    p_jd.add_argument("--status", default="done")
    p_jc = job_sub.add_parser("claim", parents=[shared])
    p_jc.add_argument("--id", required=True)
    p_jc.add_argument("--rev", type=int, required=True)

    p_recall = sub.add_parser("recall", parents=[shared])
    p_recall.add_argument("query")
    p_recall.add_argument("--budget", type=int, default=DEFAULT_LOAD_BUDGET)

    p_link = sub.add_parser("link", parents=[shared])
    p_link.add_argument("--from", dest="from_ref", required=True)
    p_link.add_argument("--to", dest="to_ref", required=True)
    p_link.add_argument("--rel", default="TRIGGERS")
    p_link.add_argument("--confirm", action="store_true")
    p_link.add_argument("--role", default=None)
    p_link.add_argument("--pointer", default=None)
    p_link.add_argument("--kind", dest="subtype", default=None)
    p_link.add_argument("--knowledge-id", dest="knowledge_id", default=None)
    p_link.add_argument("--from-type", dest="from_type", default=None)
    p_link.add_argument("--to-type", dest="to_type", default=None)
    p_link.add_argument("--sense", default=None)
    p_link.add_argument("--label", default=None)

    p_forbid = sub.add_parser("forbid", parents=[shared])
    p_forbid.add_argument("--from", dest="from_ref", required=True)
    p_forbid.add_argument("--to", dest="to_ref", required=True)
    p_forbid.add_argument("--reason", required=True)
    p_forbid.add_argument("--from-type", dest="from_type", default=None)
    p_forbid.add_argument("--to-type", dest="to_type", default=None)

    p_confirm = sub.add_parser("confirm", parents=[shared])
    p_confirm.add_argument("--from", dest="from_ref", required=True)
    p_confirm.add_argument("--to", dest="to_ref", required=True)
    p_confirm.add_argument("--rel", default="TRIGGERS")

    p_map = sub.add_parser("map", parents=[shared])
    map_sub = p_map.add_subparsers(dest="map_cmd")
    map_sub.add_parser("status")

    p_archive = sub.add_parser("archive", parents=[shared])
    p_archive.add_argument("--id", dest="knowledge_id", required=True)

    p_artifact = sub.add_parser("artifact", parents=[shared])
    artifact_sub = p_artifact.add_subparsers(dest="artifact_cmd", required=True)
    p_artifact_load = artifact_sub.add_parser("load")
    p_artifact_load.add_argument("artifact_id")

    p_measure = sub.add_parser("measure", parents=[shared])
    p_measure.add_argument("--eval", dest="do_eval", action="store_true")
    p_measure.add_argument("--improve", dest="do_improve", action="store_true")
    p_measure.add_argument("--cases", dest="cases_file", default=None)
    p_measure.add_argument("--limit", type=int, default=50)

    p_memory = sub.add_parser("memory", parents=[shared])
    memory_sub = p_memory.add_subparsers(dest="memory_cmd", required=True)
    memory_sub.add_parser("status")
    p_mw = memory_sub.add_parser("worker")
    p_mw.add_argument("--limit", type=int, default=20)
    p_mw.add_argument("--no-sweep", action="store_true")
    p_mc = memory_sub.add_parser("candidates")
    p_mc.add_argument("--status", default="pending")
    p_mc.add_argument("--limit", type=int, default=50)
    p_mp = memory_sub.add_parser("promote")
    p_mp.add_argument("candidate_id")

    p_persona = sub.add_parser("persona", parents=[shared])
    persona_sub = p_persona.add_subparsers(dest="persona_cmd", required=True)
    p_persona_export = persona_sub.add_parser("export")
    p_persona_export.add_argument("--source", default=None)
    p_persona_export.add_argument("--output", default=None)
    p_persona_export.add_argument("--budget", type=int, default=persona.DEFAULT_TOKEN_BUDGET)
    persona_sub.add_parser("show")
    persona_sub.add_parser("validate")
    p_persona_set = persona_sub.add_parser("set")
    p_persona_set.add_argument("--id", required=True)
    p_persona_set.add_argument("--title", required=True)
    p_persona_set.add_argument("--body", default=None)
    p_persona_set.add_argument("--body-file", dest="body_file", default=None)
    p_persona_set.add_argument("--priority", type=int, default=100)
    p_persona_update = persona_sub.add_parser("update")
    p_persona_update.add_argument("--id", required=True)
    p_persona_update.add_argument("--title", default=None)
    p_persona_update.add_argument("--body", default=None)
    p_persona_update.add_argument("--body-file", dest="body_file", default=None)
    p_persona_update.add_argument("--priority", type=int, default=None)
    p_persona_candidates = persona_sub.add_parser("candidates")
    p_persona_candidates.add_argument("--status", default="pending")
    p_persona_candidates.add_argument("--limit", type=int, default=50)
    p_persona_revisions = persona_sub.add_parser("revisions")
    p_persona_revisions.add_argument("--limit", type=int, default=50)
    p_persona_worker = persona_sub.add_parser("worker")
    p_persona_worker.add_argument("--limit", type=int, default=20)
    p_persona_worker.add_argument("--no-auto-apply", action="store_true")
    p_persona_rollback = persona_sub.add_parser("rollback")
    p_persona_rollback.add_argument("revision_id")
    p_persona_reject = persona_sub.add_parser("reject")
    p_persona_reject.add_argument("candidate_id")

    p_embed = sub.add_parser("embed", parents=[shared])
    embed_sub = p_embed.add_subparsers(dest="embed_cmd", required=True)
    embed_sub.add_parser("status")
    p_backfill = embed_sub.add_parser("backfill")
    p_backfill.add_argument("--kind", default=None)
    p_backfill.add_argument("--limit", type=int, default=100)

    args = parser.parse_args(argv)
    cid = getattr(args, "conversation_id", None)
    generation_id = getattr(args, "generation_id", None)
    ws = args.workspace
    cmd = args.cmd

    if cmd == "whoami":
        _print(api.whoami(workspace=ws, conversation_id=cid))
    elif cmd == "status":
        _print(api.status(workspace=ws, conversation_id=cid))
    elif cmd == "open":
        from lucid_memories.web import dashboard

        return dashboard.run_dashboard(
            host=args.host,
            port=args.port,
            open_browser=True,
        )
    elif cmd == "dashboard":
        from lucid_memories.web import dashboard

        open_browser = args.open_browser or (args.action == "open")
        return dashboard.run_dashboard(
            host=args.host,
            port=args.port,
            open_browser=open_browser,
        )
    elif cmd == "search":
        _print(
            api.search(
                args.query,
                workspace=ws,
                limit=args.limit,
                kind=args.kind,
                conversation_id=cid,
                generation_id=generation_id,
                source="cli",
            )
        )
    elif cmd == "vsearch":
        _print(
            api.semantic_search(
                args.query,
                workspace=ws,
                kind=args.kind,
                limit=args.limit,
                min_score=args.min_score,
                conversation_id=cid,
                generation_id=generation_id,
                source="cli",
            )
        )
    elif cmd == "list":
        _print(api.list_knowledge(kind=args.kind, workspace=ws, limit=args.limit, any_workspace=args.any_workspace))
    elif cmd == "load":
        _print(
            api.load(
                args.target,
                budget_tokens=args.budget,
                workspace=ws,
                conversation_id=cid,
                generation_id=generation_id,
                source="cli",
            )
        )
    elif cmd == "remember":
        body = args.body
        if args.body_file:
            body = Path(args.body_file).read_text(encoding="utf-8")
        _print(
            api.remember(
                args.title,
                body,
                kind=args.kind,
                scope=args.scope,
                workspace=ws,
                conversation_id=cid,
                tags=_tags(args.tags) if args.tags is not None else None,
                confidence=args.confidence,
                importance=args.importance,
                salience=args.salience,
                decay_half_life_days=args.decay_half_life_days,
                expires_at=args.expires_at,
                source_event_id=args.source_event_id,
                provenance=json.loads(args.provenance) if args.provenance else None,
                source="cli",
                knowledge_id=args.knowledge_id,
                rev=args.rev,
                created_at=args.created_at,
            )
        )
    elif cmd == "reload":
        _print(api.reload(conversation_id=cid, budget_tokens=args.budget, workspace=ws))
    elif cmd == "relay":
        if args.relay_cmd == "save":
            body = args.body or ""
            if args.body_file:
                body = Path(args.body_file).read_text(encoding="utf-8")
            _print(
                api.save_handoff(
                    args.title,
                    body,
                    workspace=ws,
                    conversation_id=cid,
                    suggested_skills=_tags(args.skills),
                    focus=args.focus,
                    scope=args.scope,
                    source="cli",
                )
            )
        elif args.relay_cmd == "load":
            _print(
                api.load_handoff(
                    pack_id=args.pack_id,
                    target=args.query,
                    workspace=ws,
                    budget_tokens=args.budget,
                )
            )
    elif cmd == "job":
        if args.job_cmd == "start":
            _print(
                api.job_start(
                    args.title,
                    kind=args.kind,
                    conversation_id=cid,
                    workspace=ws,
                    summary=args.summary,
                    source="cli",
                )
            )
        elif args.job_cmd == "update":
            _print(
                api.job_update(
                    args.id,
                    args.rev,
                    status=args.status,
                    summary=args.summary,
                    title=args.title,
                    source="cli",
                )
            )
        elif args.job_cmd == "done":
            _print(
                api.job_done(
                    args.id,
                    args.rev,
                    summary=args.summary,
                    status=args.status,
                    source="cli",
                )
            )
        elif args.job_cmd == "claim":
            _print(api.job_claim(args.id, args.rev, conversation_id=cid, workspace=ws, source="cli"))
    elif cmd == "recall":
        _print(
            graph.recall(
                args.query,
                workspace=ws,
                budget_tokens=args.budget,
                conversation_id=cid,
                generation_id=generation_id,
                source="cli",
            )
        )
    elif cmd == "link":
        _print(
            graph.link(
                args.from_ref,
                args.to_ref,
                rel=args.rel,
                confirm=args.confirm,
                role=args.role,
                pointer=args.pointer,
                subtype=args.subtype,
                knowledge_id=args.knowledge_id,
                from_type=args.from_type,
                to_type=args.to_type,
                sense=args.sense,
                label=args.label,
                workspace=ws,
                source="cli",
                conversation_id=cid,
            )
        )
    elif cmd == "forbid":
        _print(
            graph.forbid(
                args.from_ref,
                args.to_ref,
                reason=args.reason,
                workspace=ws,
                source="cli",
                from_type=args.from_type,
                to_type=args.to_type,
            )
        )
    elif cmd == "confirm":
        _print(graph.confirm(args.from_ref, args.to_ref, rel=args.rel, workspace=ws))
    elif cmd == "map":
        _print(graph.map_status())
    elif cmd == "archive":
        _print(api.archive(args.knowledge_id))
    elif cmd == "artifact":
        if args.artifact_cmd == "load":
            _print(api.load_artifact(args.artifact_id))
    elif cmd == "measure":
        if args.do_improve:
            _print(api.improve_from_logs(limit=args.limit, workspace=ws, conversation_id=cid))
        elif args.do_eval:
            cases = api.DEFAULT_EVAL_CASES
            if args.cases_file:
                cases = json.loads(Path(args.cases_file).read_text(encoding="utf-8"))
            _print(api.eval_retrieval(cases, workspace=ws, conversation_id=cid))
        else:
            _print(api.measure_retrieval(limit=args.limit))
    elif cmd == "memory":
        if args.memory_cmd == "status":
            _print(api.memory_status())
        elif args.memory_cmd == "worker":
            _print(api.memory_worker(limit=args.limit, sweep_faded=not args.no_sweep))
        elif args.memory_cmd == "candidates":
            _print(
                api.memory_candidates(
                    workspace=ws,
                    status=args.status,
                    limit=args.limit,
                )
            )
        elif args.memory_cmd == "promote":
            _print(api.promote_memory_candidate(args.candidate_id))
    elif cmd == "persona":
        if args.persona_cmd == "export":
            _print(
                persona.export_user_rules(
                    Path(args.source).expanduser() if args.source else None,
                    Path(args.output).expanduser() if args.output else None,
                    token_budget=args.budget,
                )
            )
        elif args.persona_cmd == "show":
            current = persona.load_persona()
            _print({"persona": current, "injection": persona.render_persona(current)})
        elif args.persona_cmd == "validate":
            _print(persona.validate_persona())
        elif args.persona_cmd == "set":
            body = args.body
            if args.body_file:
                body = Path(args.body_file).read_text(encoding="utf-8")
            if not body:
                parser.error("persona set には --body または --body-file が必要です")
            _print(
                persona.set_section(
                    section_id=args.id,
                    title=args.title,
                    content=body,
                    priority=args.priority,
                )
            )
        elif args.persona_cmd == "update":
            body = args.body
            if args.body_file:
                body = Path(args.body_file).read_text(encoding="utf-8")
            _print(
                persona.update_section(
                    args.id,
                    title=args.title,
                    content=body,
                    priority=args.priority,
                )
            )
        elif args.persona_cmd == "candidates":
            _print(api.persona_candidates(status=args.status, limit=args.limit))
        elif args.persona_cmd == "revisions":
            _print(api.persona_revisions(limit=args.limit))
        elif args.persona_cmd == "worker":
            _print(api.persona_worker(limit=args.limit, auto_apply=not args.no_auto_apply))
        elif args.persona_cmd == "rollback":
            _print(api.persona_rollback(args.revision_id))
        elif args.persona_cmd == "reject":
            _print(api.persona_reject(args.candidate_id))
    elif cmd == "embed":
        if args.embed_cmd == "status":
            _print(api.embedding_status())
        elif args.embed_cmd == "backfill":
            _print(
                api.backfill_embeddings(
                    workspace=ws,
                    kind=args.kind,
                    limit=args.limit,
                )
            )
    else:
        parser.error(f"unknown command {cmd}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
