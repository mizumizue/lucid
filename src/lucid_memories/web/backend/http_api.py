from __future__ import annotations

import hmac
import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

from .config import Settings
from .db import DatabaseUnavailableError
from .models import CollectionQuery, daily_days, first, parse_query, positive_int, query_from_params
from .services import DashboardService, ResourceNotFoundError


LOGGER = logging.getLogger("lucid_memories_dashboard")


class ApiError(Exception):
    def __init__(self, status: int, title: str, detail: str, error_type: str = "about:blank"):
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail
        self.error_type = error_type


def handler_factory(settings: Settings):
    service = DashboardService(settings.database)

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "LucidMemoriesDashboard/1.0"

        def do_GET(self) -> None:
            if not self.authorized():
                self.send_problem(ApiError(HTTPStatus.UNAUTHORIZED, "Unauthorized", "認証が必要です。"))
                return
            try:
                parsed = urlparse(self.path)
                if parsed.path.startswith("/api/"):
                    self.handle_api(parsed.path, parse_query(parsed.query))
                else:
                    self.serve_frontend(parsed.path)
            except ApiError as exc:
                self.send_problem(exc)
            except ResourceNotFoundError as exc:
                self.send_problem(ApiError(HTTPStatus.NOT_FOUND, "Not Found", str(exc)))
            except ValueError as exc:
                self.send_problem(
                    ApiError(HTTPStatus.BAD_REQUEST, "Bad Request", str(exc), "urn:problem:invalid-query")
                )
            except DatabaseUnavailableError:
                self.send_problem(
                    ApiError(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "Database Unavailable",
                        "lucid-memories database を利用できません。",
                        "urn:problem:database-unavailable",
                    )
                )
            except Exception:
                LOGGER.exception("Unhandled dashboard request")
                self.send_problem(
                    ApiError(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "Internal Server Error",
                        "ダッシュボードの処理中にエラーが発生しました。",
                        "urn:problem:internal",
                    )
                )

        def authorized(self) -> bool:
            if not settings.dashboard_token:
                return True
            supplied = self.headers.get("Authorization", "")
            if supplied.startswith("Bearer "):
                supplied = supplied.removeprefix("Bearer ").strip()
            supplied = (
                supplied
                or self.headers.get("X-Lucid-Memories-Dashboard-Token", "")
                or self.headers.get("X-Agent-Bus-Dashboard-Token", "")
            )
            return hmac.compare_digest(supplied, settings.dashboard_token)

        def handle_api(self, path: str, params: dict[str, list[str]]) -> None:
            if not path.startswith("/api/v1/"):
                raise ApiError(HTTPStatus.NOT_FOUND, "Not Found", "API が見つかりません。")
            self.handle_v1(path.removeprefix("/api/v1/").strip("/"), params)

        def handle_v1(self, resource: str, params: dict[str, list[str]]) -> None:
            if resource == "health":
                self.send_json(service.health())
                return
            if resource == "overview":
                self.send_json(service.overview())
                return
            if resource == "analytics/daily":
                self.send_json(service.daily(daily_days(params)))
                return

            if resource == "recall-graph":
                self.send_json(
                    service.recall_graph(
                        workspace=first(params, "workspace") or None,
                        conversation_id=(
                            first(params, "conversation_id")
                            or first(params, "conversation")
                            or None
                        ),
                        since=first(params, "since") or None,
                        until=first(params, "until") or None,
                        limit=positive_int(params, "limit", 200, 2000),
                    )
                )
                return

            if resource == "memory/status":
                self.send_json(service.memory_status())
                return
            if resource == "memory/candidates":
                self.send_json(service.memory_candidates(query_from_params(params)))
                return
            if resource == "memory/tasks":
                self.send_json(service.memory_tasks(query_from_params(params)))
                return
            if resource == "map/status":
                self.send_json(service.map_status())
                return
            if resource == "knowledge":
                self.send_json(service.knowledge(query_from_params(params)))
                return
            if resource.startswith("knowledge/"):
                self.send_json(service.knowledge_item(unquote(resource.split("/", 1)[1])))
                return

            if resource == "sessions":
                self.send_json(service.sessions(query_from_params(params)))
                return
            if resource.startswith("sessions/"):
                parts = resource.split("/")
                conversation_id = unquote(parts[1]) if len(parts) > 1 else ""
                if len(parts) == 2:
                    self.send_json(service.session(conversation_id))
                    return
                query = query_from_params(params)
                related = {
                    "jobs": service.session(conversation_id)["relationships"]["jobs"],
                    "events": service.session(conversation_id)["relationships"]["events"],
                    "artifacts": service.session(conversation_id)["relationships"]["artifacts"],
                }
                if len(parts) == 3 and parts[2] in related:
                    self.send_json({"data": related[parts[2]], "pagination": query.page.metadata(len(related[parts[2]]))})
                    return

            if resource == "jobs":
                self.send_json(service.jobs(query_from_params(params)))
                return
            if resource == "events":
                self.send_json(service.events(query_from_params(params)))
                return
            if resource == "artifacts":
                self.send_json(service.artifacts(query_from_params(params)))
                return
            if resource.startswith("artifacts/"):
                self.send_json(service.artifact(unquote(resource.split("/", 1)[1])))
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "Not Found", "リソースが見つかりません。")

        def send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_problem(self, error: ApiError) -> None:
            payload = {
                "type": error.error_type,
                "title": error.title,
                "status": error.status,
                "detail": error.detail,
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(error.status)
            self.send_header("Content-Type", "application/problem+json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if error.status == HTTPStatus.UNAUTHORIZED:
                self.send_header("WWW-Authenticate", "Bearer")
            self.end_headers()
            self.wfile.write(body)

        def serve_frontend(self, requested_path: str) -> None:
            dist = settings.frontend_dist

            relative = requested_path.removeprefix("/").strip()
            if not relative:
                relative = "index.html"
            candidate = safe_path(dist, relative)
            if (candidate is None or not candidate.is_file()) and "." not in Path(relative).name:
                candidate = safe_path(dist, "index.html")
            if candidate is None or not candidate.is_file():
                self.send_problem(ApiError(HTTPStatus.NOT_FOUND, "Not Found", "フロントエンドのビルドが見つかりません。"))
                return
            body = candidate.read_bytes()
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if candidate.suffix == ".js":
                content_type = "application/javascript"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            LOGGER.info("%s - %s", self.address_string(), format % args)

    return DashboardHandler


def safe_path(root: Path, relative: str) -> Path | None:
    try:
        candidate = (root / relative).resolve()
        if candidate != root.resolve() and not candidate.is_relative_to(root.resolve()):
            return None
        return candidate
    except (OSError, ValueError):
        return None

