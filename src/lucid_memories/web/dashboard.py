"""Launch the built React dashboard."""
from __future__ import annotations

import argparse
import threading
import webbrowser
from dataclasses import replace
from http.server import ThreadingHTTPServer

from .backend.config import Settings
from .backend.http_api import handler_factory


def run_dashboard(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> int:
    settings = replace(Settings.from_env(), host=host, port=port)
    settings.validate()
    if not (settings.frontend_dist / "index.html").is_file():
        print(
            f"[warn] Frontend build not found at {settings.frontend_dist}. "
            "Run 'npm --prefix src/lucid_memories/web/frontend run build' to build React frontend."
        )
    handler = handler_factory(settings)
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    url = f"http://{settings.host}:{server.server_port}/"
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
    parser = argparse.ArgumentParser(description="Serve the lucid-memories dashboard")
    parser.add_argument(
        "action",
        nargs="?",
        default=None,
        help="Optional action (e.g. 'open' to launch browser)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", dest="open_browser", action="store_true")
    args = parser.parse_args(argv)
    open_browser = args.open_browser or (args.action == "open")
    return run_dashboard(
        host=args.host,
        port=args.port,
        open_browser=open_browser,
    )


if __name__ == "__main__":
    raise SystemExit(main())
