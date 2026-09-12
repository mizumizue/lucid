#!/usr/bin/env python3
"""One-shot: import legacy idea/*.md files into lucid-memories as kind=idea."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from lucid_memories.api import list_knowledge, remember  # noqa: E402


SOURCES = [
    (
        Path.home() / ".cursor/outputs/ideas/2026-07-19-gcp-ops-orchestrator.md",
        "global",
        None,
        "2026-07-19T00:00:00+00:00",
    ),
    (
        Path.home() / "Cursor/idea/2026-07-07-chrome-download-path-by-domain.md",
        "workspace",
        str(Path.home() / "Cursor"),
        "2026-07-07T00:00:00+00:00",
    ),
    (
        Path.home()
        / "Cursor-Workspace/steam-repo/idea/winter-memories/2026-08-23-winter-memories-jp-patch-uncensor.md",
        "workspace",
        str(Path.home() / "Cursor-Workspace/steam-repo"),
        "2026-08-23T00:00:00+00:00",
    ),
    (
        Path.home()
        / "Cursor-Workspace/steam-repo/idea/with-the-devilish-her/2026-08-21-uncensor-apply-plan.md",
        "workspace",
        str(Path.home() / "Cursor-Workspace/steam-repo"),
        "2026-08-21T00:00:00+00:00",
    ),
]


def parse_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def parse_tags(text: str) -> list[str]:
    m = re.search(r"^## タグ\s*$", text, re.M)
    if not m:
        return ["imported"]
    rest = text[m.end() :].lstrip("\n")
    first = rest.splitlines()[0] if rest else ""
    tags = [t.strip() for t in first.replace("、", ",").split(",") if t.strip()]
    if "imported" not in tags:
        tags.append("imported")
    return tags


def already_imported(title: str) -> bool:
    listed = list_knowledge(kind="idea", limit=200, any_workspace=True)
    for item in listed.get("knowledge") or []:
        if item.get("title") == title:
            return True
    return False


def main() -> int:
    imported = []
    skipped = []
    missing = []
    for path, scope, workspace, created_at in SOURCES:
        if not path.is_file():
            missing.append(str(path))
            continue
        text = path.read_text(encoding="utf-8")
        title = parse_title(text, path.stem)
        if already_imported(title):
            skipped.append(title)
            continue
        result = remember(
            title,
            text,
            kind="idea",
            scope=scope,
            workspace=workspace,
            tags=parse_tags(text),
            created_at=created_at,
            source="cli",
        )
        if not result.get("ok"):
            print(result)
            return 1
        imported.append({"id": result["id"], "title": title, "path": str(path)})
    print({"imported": imported, "skipped": skipped, "missing": missing})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
