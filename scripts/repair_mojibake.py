#!/usr/bin/env python3
"""Repair mojibake data in lucid-memories SQLite database using agent-transcripts."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add src to sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lucid_memories.core.api import _prompt_query
from lucid_memories.storage.paths import db_path

MOJIBAKE_CHARS = set("縺荳隕繝繧縲縢豁螳蟇菴\ufffd")


def is_mojibake(text: str | None) -> bool:
    if not text:
        return False
    return any(ch in text for ch in MOJIBAKE_CHARS)


def load_transcripts(transcripts_base: Path) -> dict[str, list[str]]:
    cid_to_prompts: dict[str, list[str]] = {}
    for root, _, files in os.walk(transcripts_base):
        for f in files:
            if f.endswith(".jsonl"):
                cid = f[:-6]
                path = os.path.join(root, f)
                prompts: list[str] = []
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        for line in fp:
                            data = json.loads(line)
                            if data.get("role") == "user":
                                for c in data.get("message", {}).get("content", []):
                                    t = c.get("text", "")
                                    if "<user_query>" in t:
                                        q = (
                                            t.split("<user_query>")[1]
                                            .split("</user_query>")[0]
                                            .strip()
                                        )
                                        prompts.append(q)
                except Exception:
                    pass
                if prompts:
                    cid_to_prompts[cid] = prompts
    return cid_to_prompts


def find_best_matching_prompt(mojibake_text: str, candidates: list[str]) -> str | None:
    # First, try cp932 -> utf-8 conversion
    try:
        raw_bytes = mojibake_text.encode("cp932", errors="ignore")
        decoded = raw_bytes.decode("utf-8", errors="ignore")
        if decoded:
            for cand in candidates:
                # check if there's significant overlap
                sub = decoded[:10]
                if sub and sub in cand:
                    return cand
    except Exception:
        pass

    # Heuristic match based on key markers
    if "繝ｪ繝昴ず繝医Μ" in mojibake_text:
        for cand in candidates:
            if "リポジトリ" in cand:
                return cand
    if "隕∽ｻｶ繝ｻ莉墓ｧ倥" in mojibake_text:
        for cand in candidates:
            if "要件・仕様" in cand:
                return cand
    if "縺薙％縺ｧ縺・" in mojibake_text:
        for cand in candidates:
            if "ここでいうテストケース" in cand:
                return cand
    if "荳願ｨ倥・" in mojibake_text:
        for cand in candidates:
            if "上記の説明はREADME.md" in cand:
                return cand
    if "霑ｽ蜉" in mojibake_text:
        for cand in candidates:
            if "追加" in cand:
                return cand
    if "遘ｻ陦" in mojibake_text:
        for cand in candidates:
            if "移行" in cand:
                return cand
    if "縺倥ｃ縺ゅ◎繧後〒" in mojibake_text:
        for cand in candidates:
            if "じゃあそれで" in cand:
                return cand
    if "lucid" in mojibake_text and "縺ｨ縺ｯ" in mojibake_text:
        for cand in candidates:
            if "lucid とは" in cand or "lucidとは" in cand:
                return cand
    if "荳崎ｶｳ" in mojibake_text:
        for cand in candidates:
            if "不足" in cand:
                return cand

    return None


def main() -> None:
    database_file = db_path()
    if not database_file.exists():
        print(f"Database not found at {database_file}")
        sys.exit(1)

    # 1. Backup
    backup_path = database_file.with_name(
        f"lucid-memories.backup.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.sqlite"
    )
    shutil.copy2(database_file, backup_path)
    print(f"Created backup at {backup_path}")

    # 2. Load transcripts
    user_home = Path.home()
    transcripts_base = user_home / ".cursor" / "projects"
    cid_to_prompts = load_transcripts(transcripts_base)
    print(f"Loaded transcripts for {len(cid_to_prompts)} conversations.")

    conn = sqlite3.connect(database_file)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 3. Repair retrieval_logs
    print("\n--- Repairing retrieval_logs ---")
    cur.execute("SELECT id, request_id, conversation_id, prompt FROM retrieval_logs")
    log_rows = cur.fetchall()
    repaired_logs = 0
    req_to_new_query: dict[str, str] = {}
    for row in log_rows:
        lid = row["id"]
        req_id = row["request_id"]
        cid = row["conversation_id"]
        prompt = row["prompt"]
        if is_mojibake(prompt) and cid in cid_to_prompts:
            match = find_best_matching_prompt(prompt, cid_to_prompts[cid])
            if match:
                new_q = _prompt_query(match)
                cur.execute(
                    "UPDATE retrieval_logs SET prompt = ?, query = ? WHERE id = ?",
                    (match, new_q, lid),
                )
                if req_id:
                    req_to_new_query[str(req_id)] = new_q
                repaired_logs += 1
                print(f"  [LOG {lid}] Repaired prompt to: {match[:40]!r}")
    print(f"Repaired {repaired_logs} rows in retrieval_logs.")

    # 4. Repair retrieval_requests
    print("\n--- Repairing retrieval_requests ---")
    cur.execute("SELECT id, conversation_id, query FROM retrieval_requests")
    req_rows = cur.fetchall()
    repaired_reqs = 0
    for row in req_rows:
        rid = str(row["id"])
        cid = row["conversation_id"]
        query = row["query"]
        if rid in req_to_new_query:
            cur.execute(
                "UPDATE retrieval_requests SET query = ? WHERE id = ?",
                (req_to_new_query[rid], rid),
            )
            repaired_reqs += 1
            print(f"  [REQ {rid}] Repaired query to: {req_to_new_query[rid]!r}")
        elif is_mojibake(query) and cid in cid_to_prompts:
            match = find_best_matching_prompt(query, cid_to_prompts[cid])
            if match:
                new_q = _prompt_query(match)
                cur.execute(
                    "UPDATE retrieval_requests SET query = ? WHERE id = ?",
                    (new_q, rid),
                )
                repaired_reqs += 1
                print(f"  [REQ {rid}] Repaired query to: {new_q!r}")
    print(f"Repaired {repaired_reqs} rows in retrieval_requests.")

    # 5. Repair turn_state
    print("\n--- Repairing turn_state ---")
    cur.execute("SELECT conversation_id, last_prompt FROM turn_state")
    ts_rows = cur.fetchall()
    repaired_ts = 0
    for row in ts_rows:
        cid = row["conversation_id"]
        lp = row["last_prompt"]
        if is_mojibake(lp) and cid in cid_to_prompts:
            match = find_best_matching_prompt(lp, cid_to_prompts[cid])
            if not match and cid_to_prompts[cid]:
                match = cid_to_prompts[cid][-1]
            if match:
                cur.execute(
                    "UPDATE turn_state SET last_prompt = ? WHERE conversation_id = ?",
                    (match, cid),
                )
                repaired_ts += 1
                print(f"  [TURN_STATE {cid}] Repaired last_prompt to: {match[:40]!r}")
    print(f"Repaired {repaired_ts} rows in turn_state.")

    # 6. Repair conversation_events
    print("\n--- Repairing conversation_events ---")
    cur.execute(
        "SELECT id, conversation_id, event_type, input_text FROM conversation_events WHERE event_type = 'beforeSubmitPrompt'"
    )
    ev_rows = cur.fetchall()
    repaired_ev = 0
    for row in ev_rows:
        eid = row["id"]
        cid = row["conversation_id"]
        inp = row["input_text"]
        if is_mojibake(inp) and cid in cid_to_prompts:
            match = find_best_matching_prompt(inp, cid_to_prompts[cid])
            if match:
                cur.execute(
                    "UPDATE conversation_events SET input_text = ? WHERE id = ?",
                    (match, eid),
                )
                repaired_ev += 1
                print(f"  [EVENT {eid}] Repaired input_text to: {match[:40]!r}")
    print(f"Repaired {repaired_ev} beforeSubmitPrompt events.")

    conn.commit()
    conn.close()
    print("\nDatabase repair completed successfully!")


if __name__ == "__main__":
    main()
