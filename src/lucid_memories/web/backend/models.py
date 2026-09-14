from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs


@dataclass(frozen=True)
class Page:
    number: int = 1
    size: int = 25

    @property
    def offset(self) -> int:
        return (self.number - 1) * self.size

    def metadata(self, total: int) -> dict[str, int | bool]:
        return {
            "page": self.number,
            "limit": self.size,
            "total": total,
            "pages": (total + self.size - 1) // self.size if total else 0,
            "has_next": self.offset + self.size < total,
            "has_previous": self.number > 1,
        }


@dataclass(frozen=True)
class CollectionQuery:
    text: str = ""
    status: str = ""
    model: str = ""
    session_kind: str = ""
    origin: str = ""
    scope: str = ""
    start: str = ""
    end: str = ""
    sort: str = ""
    page: Page = Page()


def query_from_params(params: dict[str, list[str]]) -> CollectionQuery:
    return CollectionQuery(
        text=first(params, "q"),
        status=first(params, "status"),
        model=first(params, "model"),
        session_kind=first(params, "kind"),
        origin=first(params, "origin"),
        scope=first(params, "scope"),
        start=first(params, "from"),
        end=first(params, "to"),
        sort=first(params, "sort"),
        page=Page(
            number=positive_int(params, "page", 1, 100_000),
            size=positive_int(params, "limit", 25, 100),
        ),
    )


def first(params: dict[str, list[str]], name: str) -> str:
    return params.get(name, [""])[0].strip()


def positive_int(
    params: dict[str, list[str]],
    name: str,
    default: int,
    maximum: int,
) -> int:
    value = first(params, name)
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} は整数で指定してください。") from exc
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"{name} は 1-{maximum} の範囲で指定してください。")
    return parsed


def daily_days(params: dict[str, list[str]]) -> int:
    value = first(params, "days") or "14"
    try:
        days = int(value)
    except ValueError as exc:
        raise ValueError("days は整数で指定してください。") from exc
    if not 3 <= days <= 60:
        raise ValueError("days は 3-60 の範囲で指定してください。")
    return days


def parse_query(query: str) -> dict[str, list[str]]:
    return parse_qs(query, keep_blank_values=True)

