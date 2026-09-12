from __future__ import annotations

import hashlib
import json
import math
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from lucid_memories.storage.paths import env


DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_SECONDS = 8


class EmbeddingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Embedding:
    model: str
    values: list[float]

    @property
    def dimensions(self) -> int:
        return len(self.values)


def provider() -> str:
    return (env("EMBED_PROVIDER", "ollama") or "ollama").strip().lower()


def model() -> str:
    return (env("EMBED_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def endpoint() -> str:
    return (env("OLLAMA_URL", DEFAULT_ENDPOINT) or DEFAULT_ENDPOINT).rstrip("/")


def keep_alive() -> str | int:
    raw = (env("EMBED_KEEP_ALIVE", "-1") or "-1").strip()
    if raw == "-1":
        return -1
    return raw


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def unpack(raw: bytes, dimensions: int) -> list[float]:
    expected = dimensions * 4
    if len(raw) != expected:
        raise EmbeddingError(
            f"invalid vector bytes: expected={expected}, actual={len(raw)}"
        )
    return list(struct.unpack(f"<{dimensions}f", raw))


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _request(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{endpoint()}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=float(
                env("EMBED_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))
                or str(DEFAULT_TIMEOUT_SECONDS)
            ),
        ) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        finally:
            exc.close()
        raise EmbeddingError(
            f"embedding provider returned HTTP {exc.code}: {detail[:240]}"
        ) from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise EmbeddingError(
            f"embedding provider unavailable at {endpoint()}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise EmbeddingError("embedding provider returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise EmbeddingError("embedding provider returned a non-object")
    return decoded


def embed(text: str) -> Embedding:
    clean = (text or "").strip()
    if not clean:
        raise EmbeddingError("cannot embed empty text")
    selected = provider()
    if selected != "ollama":
        raise EmbeddingError(f"unsupported embedding provider: {selected}")

    # /api/embed accepts a batch and is the current Ollama endpoint.
    try:
        response = _request(
            "/api/embed",
            {"model": model(), "input": [clean], "keep_alive": keep_alive()},
        )
        values = response.get("embeddings")
        if isinstance(values, list) and values and isinstance(values[0], list):
            return Embedding(model=model(), values=[float(v) for v in values[0]])
    except EmbeddingError as first_error:
        # Older Ollama versions expose /api/embeddings instead.
        try:
            response = _request(
                "/api/embeddings",
                {"model": model(), "prompt": clean, "keep_alive": keep_alive()},
            )
            values = response.get("embedding")
            if isinstance(values, list):
                return Embedding(model=model(), values=[float(v) for v in values])
        except EmbeddingError:
            raise first_error

    raise EmbeddingError("embedding provider returned no embedding vector")


def status() -> dict[str, Any]:
    try:
        response = _request("/api/show", {"name": model()})
        return {
            "ok": True,
            "provider": provider(),
            "model": model(),
            "dimensions": response.get("details", {}).get("embedding_length"),
        }
    except EmbeddingError as exc:
        return {
            "ok": False,
            "provider": provider(),
            "model": model(),
            "error": str(exc),
        }
