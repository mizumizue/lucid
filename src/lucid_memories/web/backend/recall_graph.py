from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .db import DatabaseUnavailableError


def build_recall_graph(
    database: Path,
    *,
    workspace: str | None = None,
    conversation_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Build the shared lucid-memories conversation-recall graph."""
    try:
        from lucid_memories.core.api import dashboard_graph
    except ImportError:
        try:
            from lucid_memories.api import dashboard_graph
        except ImportError:
            package_root = Path.home() / ".cursor" / "lucid-memories"
            legacy_root = Path.home() / ".cursor" / "agent-bus"
            for root in (package_root, legacy_root):
                for candidate in (root / "src", root):
                    if (candidate / "lucid_memories").is_dir():
                        candidate_str = str(candidate)
                        if candidate_str not in sys.path:
                            sys.path.insert(0, candidate_str)
                        break
                else:
                    continue
                break
            try:
                from lucid_memories.api import dashboard_graph
            except ImportError as exc:
                raise DatabaseUnavailableError("lucid-memories のグラフ実装を読み込めません。") from exc

    try:
        return dashboard_graph(
            database=database,
            workspace=workspace,
            conversation_id=conversation_id,
            since=since,
            until=until,
            limit=limit,
        )
    except FileNotFoundError as exc:
        raise DatabaseUnavailableError("lucid-memories database が見つかりません。") from exc
