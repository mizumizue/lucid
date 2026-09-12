from __future__ import annotations

import logging
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

# Ensure src is in sys.path when running standalone
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from lucid_memories.web.backend.config import Settings
from lucid_memories.web.backend.http_api import handler_factory


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    settings.validate()
    handler = handler_factory(settings)
    print(f"lucid-memories Dashboard: http://{settings.host}:{settings.port}")
    print(f"SQLite (read-only): {settings.database}")
    try:
        ThreadingHTTPServer((settings.host, settings.port), handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server...")


if __name__ == "__main__":
    main()
