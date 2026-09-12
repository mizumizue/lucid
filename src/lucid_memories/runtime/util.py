from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from lucid_memories.storage.paths import STALE_AFTER_SECONDS, SUMMARY_LIMIT


def new_id() -> str:
    return str(uuid.uuid4())


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def truncate(text: str | None, limit: int = SUMMARY_LIMIT) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def normalize_root(path: str | None) -> str:
    if not path:
        return ""
    raw = path.strip().strip('"')
    posix = raw.replace("\\", "/")
    # Handle /C:/path or /c:/path or C:/path
    drive_colon = re.match(r"^/?([a-zA-Z]):/(.*)$", posix)
    if drive_colon:
        raw = drive_colon.group(1).upper() + ":\\" + drive_colon.group(2).replace("/", "\\")
    else:
        # Handle /c/path (msys / git-bash style)
        msys = re.match(r"^/([a-zA-Z])/(.*)$", posix)
        if msys:
            raw = msys.group(1).upper() + ":\\" + msys.group(2).replace("/", "\\")
    expanded = os.path.abspath(os.path.expanduser(raw))
    return os.path.normcase(expanded).rstrip("\\/")


def parse_roots(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [normalize_root(str(x)) for x in data if x]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def row_dict(row: Any) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_stale_heartbeat(last_heartbeat_at: str | None, now: datetime | None = None) -> bool:
    ts = parse_iso(last_heartbeat_at)
    if ts is None:
        return True
    now = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return now - ts > timedelta(seconds=STALE_AFTER_SECONDS)


def _path_boundary_prefix(left: str, right: str) -> bool:
    if left == right:
        return True
    for sep in {os.sep, "/", "\\"}:
        if left.startswith(right + sep) or right.startswith(left + sep):
            return True
    return False


def workspace_matches(roots: list[str], workspace: str | None) -> bool:
    if not workspace:
        return True
    target = normalize_root(workspace)
    if not target:
        return True
    for root in roots:
        if not root:
            continue
        if _path_boundary_prefix(root, target):
            return True
    return False


def workspace_contains(roots: list[str], workspace: str | None) -> bool:
    """Return whether workspace is equal to or below a registered root."""
    if not workspace:
        return True
    target = normalize_root(workspace)
    if not target:
        return True
    for root in roots:
        root = normalize_root(root)
        if not root:
            continue
        if root == target:
            return True
        for sep in {os.sep, "/", "\\"}:
            if target.startswith(root + sep):
                return True
    return False


_HIRA_PARTICLES = ("から", "まで", "より", "を", "に", "が", "は", "の", "と", "で", "も", "へ", "や")
_HIRA_STOP = frozenset((*_HIRA_PARTICLES, "して", "ます", "です", "こと", "もの", "ため", "よう"))


def _script_kind(ch: str) -> str:
    if ch.isascii() and (ch.isalnum() or ch in "_-"):
        return "latin"
    if "ァ" <= ch <= "ン" or ch in "ーヴヵヶ":
        return "kata"
    if "ぁ" <= ch <= "ん":
        return "hira"
    if "一" <= ch <= "龯" or ch == "々":
        return "kanji"
    return "skip"


def _split_hira(text: str) -> list[str]:
    if not text:
        return []
    pattern = "|".join(sorted(_HIRA_PARTICLES, key=len, reverse=True))
    parts = re.split(f"({pattern})", text)
    return [p for p in parts if p]


def _script_runs(prompt: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    buf: list[str] = []
    kind: str | None = None
    for ch in prompt or "":
        next_kind = _script_kind(ch)
        if next_kind == "skip":
            if buf and kind:
                runs.append((kind, "".join(buf)))
            buf = []
            kind = None
            continue
        if kind is None or next_kind == kind:
            kind = next_kind
            buf.append(ch)
            continue
        runs.append((kind, "".join(buf)))
        buf = [ch]
        kind = next_kind
    if buf and kind:
        runs.append((kind, "".join(buf)))
    return runs


def query_tokens(prompt: str) -> list[str]:
    """Split on script boundaries. Kanji+okurigana stay together (提案書き込み → 提案, 書き, 込み)."""
    pieces: list[str] = []
    runs = _script_runs(prompt)
    i = 0
    while i < len(runs):
        kind, text = runs[i]
        nxt = runs[i + 1] if i + 1 < len(runs) else None
        if kind == "kanji" and nxt and nxt[0] == "hira":
            hira_parts = _split_hira(nxt[1])
            first = hira_parts[0] if hira_parts else ""
            if not first or first in _HIRA_PARTICLES:
                pieces.append(text)
                pieces.extend(hira_parts)
                i += 2
                continue
            if len(text) > 1:
                pieces.append(text[:-1])
                stem = text[-1]
            else:
                stem = text
            pieces.append(stem + first)
            pieces.extend(hira_parts[1:])
            i += 2
            continue
        if kind == "hira":
            pieces.extend(_split_hira(text))
            i += 1
            continue
        pieces.append(text)
        i += 1
    return [tok for tok in pieces if len(tok) >= 2]


def useful_query_tokens(query: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tok in query_tokens(query):
        if tok in _HIRA_STOP:
            continue
        key = tok.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
        if len(out) >= 8:
            break
    return out


def fts_match_arg(query: str) -> str | None:
    parts: list[str] = []
    for tok in useful_query_tokens(query):
        parts.append(f'"{tok.replace(chr(34), chr(34) * 2)}"')
    if parts:
        return " OR ".join(parts)
    cleaned = query.strip()
    if not cleaned:
        return None
    return f'"{cleaned.replace(chr(34), chr(34) * 2)}"'


def looks_like_id(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            value.strip(),
        )
    )
