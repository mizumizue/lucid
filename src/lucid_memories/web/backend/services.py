from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .db import DatabaseInfo, connect, inspect
from .models import CollectionQuery
from .repositories import DashboardRepository
from .recall_graph import build_recall_graph


class ResourceNotFoundError(LookupError):
    pass


class DashboardService:
    def __init__(self, database: Path):
        self.database = database

    @contextmanager
    def repository(self) -> Iterator[DashboardRepository]:
        connection = connect(self.database)
        try:
            yield DashboardRepository(connection, inspect(connection, self.database))
        finally:
            connection.close()

    def overview(self) -> dict:
        with self.repository() as repository:
            return repository.overview()

    def health(self) -> dict:
        with self.repository() as repository:
            return {
                "status": "ok",
                "database": {
                    "name": repository.info.path.name,
                    "schema_version": repository.info.schema_version,
                    "read_only": True,
                },
                "capabilities": repository.capabilities(),
            }

    def sessions(self, query: CollectionQuery) -> dict:
        with self.repository() as repository:
            return repository.session_rows(query)

    def jobs(self, query: CollectionQuery) -> dict:
        with self.repository() as repository:
            return repository.job_rows(query)

    def memory_status(self) -> dict:
        with self.repository() as repository:
            return repository.memory_status()

    def memory_candidates(self, query: CollectionQuery) -> dict:
        with self.repository() as repository:
            return repository.candidate_rows(query)

    def knowledge(self, query: CollectionQuery) -> dict:
        with self.repository() as repository:
            return repository.knowledge_rows(query)

    def knowledge_item(self, knowledge_id: str) -> dict:
        with self.repository() as repository:
            detail = repository.knowledge_detail(knowledge_id)
        if not detail:
            raise ResourceNotFoundError("Knowledge が見つかりません。")
        return detail

    def memory_tasks(self, query: CollectionQuery) -> dict:
        with self.repository() as repository:
            return repository.task_rows(query)

    def map_status(self) -> dict:
        with self.repository() as repository:
            return repository.map_status()

    def events(self, query: CollectionQuery) -> dict:
        with self.repository() as repository:
            return repository.event_rows(query)

    def artifacts(self, query: CollectionQuery) -> dict:
        with self.repository() as repository:
            return repository.artifact_rows(query)

    def artifact(self, artifact_id: str) -> dict:
        with self.repository() as repository:
            detail = repository.artifact_detail(artifact_id)
        if not detail:
            raise ResourceNotFoundError("Artifact が見つかりません。")
        return detail

    def daily(self, days: int) -> dict:
        with self.repository() as repository:
            return {"data": repository.daily(days), "meta": {"days": days}}

    def recall_graph(
        self,
        *,
        workspace: str | None = None,
        conversation_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 200,
    ) -> dict:
        return build_recall_graph(
            self.database,
            workspace=workspace,
            conversation_id=conversation_id,
            since=since,
            until=until,
            limit=limit,
        )

    def session(self, conversation_id: str) -> dict:
        with self.repository() as repository:
            detail = repository.session_detail(conversation_id)
        if not detail:
            raise ResourceNotFoundError("Session が見つかりません。")
        return detail

