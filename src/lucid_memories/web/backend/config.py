from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
WEB_DIR = Path(__file__).resolve().parent.parent


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return default


def default_database() -> Path:
    # 1. Respect LUCID_MEMORIES_HOME if set
    custom_home = _env("LUCID_MEMORIES_HOME")
    if custom_home:
        base = Path(custom_home).expanduser()
        for cand in (base / ".db" / "lucid-memories.sqlite", base / "bus.sqlite"):
            if cand.exists():
                return cand
        return base / ".db" / "lucid-memories.sqlite"

    canonical = Path.home() / ".cursor" / "lucid-memories" / ".db" / "lucid-memories.sqlite"
    current_legacy = Path.home() / ".cursor" / "lucid-memories" / "bus.sqlite"
    legacy = Path.home() / ".cursor" / "agent-bus" / "bus.sqlite"
    for candidate in (canonical, current_legacy, legacy):
        if candidate.exists():
            return candidate
    return canonical


def default_frontend_dist() -> Path:
    candidates = [
        WEB_DIR / "frontend" / "dist",
        WEB_DIR / "dist",
        REPO_ROOT / "src" / "lucid_memories" / "web" / "frontend" / "dist",
        REPO_ROOT / "src" / "lucid_memories" / "web" / "dist",
        REPO_ROOT / "src" / "frontend" / "dist",
        REPO_ROOT / "frontend" / "dist",
        Path.cwd() / "frontend" / "dist",
        Path.home() / ".cursor" / "lucid-memories" / "frontend" / "dist",
    ]
    for c in candidates:
        if (c / "index.html").is_file():
            return c
    return candidates[0]


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    database: Path
    frontend_dist: Path
    dashboard_token: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=_env("LUCID_MEMORIES_DASHBOARD_HOST", "AGENT_BUS_DASHBOARD_HOST", default="127.0.0.1") or "127.0.0.1",
            port=parse_port(
                _env("LUCID_MEMORIES_DASHBOARD_PORT", "AGENT_BUS_DASHBOARD_PORT", default="8765") or "8765"
            ),
            database=Path(
                _env("LUCID_MEMORIES_DB", "AGENT_BUS_DB", default=str(default_database())) or str(default_database())
            ).expanduser(),
            frontend_dist=Path(
                _env(
                    "LUCID_MEMORIES_DASHBOARD_FRONTEND_DIST",
                    "AGENT_BUS_DASHBOARD_FRONTEND_DIST",
                    default=str(default_frontend_dist()),
                )
                or str(default_frontend_dist())
            ).expanduser(),
            dashboard_token=_env("LUCID_MEMORIES_DASHBOARD_TOKEN", "AGENT_BUS_DASHBOARD_TOKEN"),
        )

    def validate(self) -> None:
        if not is_loopback_host(self.host) and not self.dashboard_token:
            raise RuntimeError(
                "外部 bind には LUCID_MEMORIES_DASHBOARD_TOKEN が必要です。"
            )


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("LUCID_MEMORIES_DASHBOARD_PORT は整数で指定してください。") from exc
    if not 1 <= port <= 65535:
        raise ValueError("LUCID_MEMORIES_DASHBOARD_PORT は 1-65535 の範囲で指定してください。")
    return port


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized in {"localhost", "ip6-localhost"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False

