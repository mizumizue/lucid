from __future__ import annotations

import os
from pathlib import Path

SCHEMA_VERSION = 14
STALE_AFTER_SECONDS = 15 * 60
DEFAULT_LOAD_BUDGET = 2000
INLINE_BODY_LIMIT = 2000
SUMMARY_LIMIT = 500
LEASE_TTL_SECONDS = 5 * 60
PRODUCT = "lucid-memories"


def env(suffix: str, default: str | None = None) -> str | None:
    val = os.environ.get("LUCID_MEMORIES_" + suffix)
    if val:
        return val
    return default


def bus_home() -> Path:
    override = env("HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cursor" / PRODUCT


def db_path() -> Path:
    return bus_home() / ".db" / "lucid-memories.sqlite"


def legacy_db_path() -> Path:
    return bus_home() / "bus.sqlite"


def map_path() -> Path:
    return bus_home() / ".db" / "map.lbdb"


def legacy_map_path() -> Path:
    return bus_home() / "map.lbdb"


def database_dir() -> Path:
    return bus_home() / ".db"


def graph_schema_path() -> Path:
    target = Path(__file__).resolve().parent / "schema.cypher"
    if target.exists():
        return target
    return Path(__file__).resolve().parents[3] / "graph" / "schema.cypher"


def blobs_dir() -> Path:
    return bus_home() / "blobs"


def schema_path() -> Path:
    target = Path(__file__).resolve().parent / "schema.sql"
    if target.exists():
        return target
    return Path(__file__).resolve().parents[3] / "schema.sql"


def logs_dir() -> Path:
    return bus_home() / "logs"
