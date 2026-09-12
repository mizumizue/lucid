from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import DatabaseUnavailableError
from lucid_memories.core.api import dashboard_graph


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
