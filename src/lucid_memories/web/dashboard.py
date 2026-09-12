"""Local read-only dashboard server for retrieval relationships."""
from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lucid_memories.core import api


HTML_PATH = Path(__file__).with_name("dashboard.html")
GUIDE_HTML_PATH = Path(__file__).with_name("guide.html")


def _first(params: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    value = params.get(key, [default])[0]
    return value or None


def _handler(
    *,
    workspace: str | None,
    conversation_id: str | None,
    since: str | None,
    until: str | None,
    limit: int,
) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "lucid-memories-dashboard/1.0"

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value: object, status: int = 200) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8", status)

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if parsed.path == "/":
                self._send(HTML_PATH.read_bytes(), "text/html; charset=utf-8")
                return
            if parsed.path == "/guide":
                self._send(GUIDE_HTML_PATH.read_bytes(), "text/html; charset=utf-8")
                return
            if parsed.path == "/health":
                self._json({"ok": True, "service": "lucid-memories-dashboard"})
                return
            if parsed.path != "/api/graph":
                self._json({"ok": False, "error": "not_found"}, 404)
                return
            try:
                raw_limit = _first(params, "limit", str(limit))
                graph = api.dashboard_graph(
                    workspace=_first(params, "workspace", workspace),
                    conversation_id=_first(
                        params,
                        "conversation_id",
                        _first(params, "conversation", conversation_id),
                    ),
                    since=_first(params, "since", since),
                    until=_first(params, "until", until),
                    limit=int(raw_limit or limit),
                )
                self._json(graph)
            except (TypeError, ValueError) as exc:
                self._json({"ok": False, "error": "invalid_parameter", "message": str(exc)}, 400)
            except Exception as exc:  # keep the local server alive for inspection
                self._json({"ok": False, "error": "dashboard_error", "message": str(exc)}, 500)

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardHandler


def run_dashboard(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    workspace: str | None = None,
    conversation_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
    open_browser: bool = False,
    console: bool = True,
) -> int:
    try:
        from .backend.config import Settings
        from .backend.http_api import handler_factory

        settings = Settings.from_env()
        # Allow overrides from CLI parameters
        if host != "127.0.0.1":
            object.__setattr__(settings, "host", host) if hasattr(settings, "__setattr__") else None
        if console and settings.frontend_dist.exists():
            handler = handler_factory(settings)
        else:
            handler = _handler(
                workspace=workspace,
                conversation_id=conversation_id,
                since=since,
                until=until,
                limit=limit,
            )
    except Exception:
        handler = _handler(
            workspace=workspace,
            conversation_id=conversation_id,
            since=since,
            until=until,
            limit=limit,
        )

    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{server.server_port}/"
    print(f"lucid-memories dashboard: {url}")
    if open_browser:
        threading.Timer(0.1, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the lucid-memories retrieval dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--conversation", dest="conversation_id", default=None)
    parser.add_argument("--since", default=None)
    parser.add_argument("--until", default=None)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--open", dest="open_browser", action="store_true")
    parser.add_argument("--graph-only", dest="graph_only", action="store_true", help="Serve lightweight single-file recall graph only")
    args = parser.parse_args(argv)
    return run_dashboard(
        host=args.host,
        port=args.port,
        workspace=args.workspace,
        conversation_id=args.conversation_id,
        since=args.since,
        until=args.until,
        limit=args.limit,
        open_browser=args.open_browser,
        console=not args.graph_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
