from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path
from typing import Protocol

from .config import Settings


class VectorIndex(Protocol):
    dimension: int

    def upsert(self, decision_id: str, embedding: list[float]) -> None: ...
    def search(self, embedding: list[float], limit: int = 10) -> list[tuple[str, float]]: ...
    def delete(self, decision_id: str) -> None: ...
    def reset(self) -> None: ...


class SQLiteVecIndex:
    """Vector index backed by the sqlite-vec extension."""

    def __init__(self, path: str | Path, dimension: int):
        self.path = str(Path(path).expanduser().resolve())
        self.dimension = dimension
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.enable_load_extension(True)
        import sqlite_vec

        sqlite_vec.load(self._connection)
        self._connection.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS decision_vectors USING vec0(
                decision_id TEXT PRIMARY KEY,
                embedding FLOAT[{self.dimension}] distance_metric=cosine
            )
            """
        )
        self._connection.commit()

    def upsert(self, decision_id: str, embedding: list[float]) -> None:
        payload = json.dumps(embedding)
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO decision_vectors(decision_id, embedding) VALUES (?, ?)",
                (decision_id, payload),
            )
            self._connection.commit()

    def search(self, embedding: list[float], limit: int = 10) -> list[tuple[str, float]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT decision_id, distance
                FROM decision_vectors
                WHERE embedding MATCH ? AND k = ?
                ORDER BY distance
                """,
                (json.dumps(embedding), limit),
            ).fetchall()
        return [(row[0], max(0.0, min(1.0, 1.0 - float(row[1])))) for row in rows]

    def delete(self, decision_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM decision_vectors WHERE decision_id = ?", (decision_id,)
            )
            self._connection.commit()

    def reset(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM decision_vectors")
            self._connection.commit()


class JSONVectorIndex:
    """Portable fallback: vectors stored as JSON and scored with cosine similarity."""

    def __init__(self, path: str | Path, dimension: int):
        self.path = str(Path(path).expanduser().resolve())
        self.dimension = dimension
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_vectors_json (
                decision_id TEXT PRIMARY KEY,
                embedding TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if not left_norm or not right_norm:
            return 0.0
        return max(0.0, min(1.0, dot / (left_norm * right_norm)))

    def upsert(self, decision_id: str, embedding: list[float]) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO decision_vectors_json(decision_id, embedding)
                VALUES (?, ?)
                """,
                (decision_id, json.dumps(embedding)),
            )
            self._connection.commit()

    def search(self, embedding: list[float], limit: int = 10) -> list[tuple[str, float]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT decision_id, embedding FROM decision_vectors_json"
            ).fetchall()
        scored = [
            (decision_id, self._cosine(embedding, json.loads(payload)))
            for decision_id, payload in rows
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def delete(self, decision_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM decision_vectors_json WHERE decision_id = ?", (decision_id,)
            )
            self._connection.commit()

    def reset(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM decision_vectors_json")
            self._connection.commit()


def build_vector_index(settings: Settings, path: str | Path | None = None) -> VectorIndex:
    target = path or settings.sqlite_path
    if settings.vector_backend in {"local", "sqlite-vec"}:
        try:
            return SQLiteVecIndex(target, settings.embedding_dim)
        except Exception:
            return JSONVectorIndex(target, settings.embedding_dim)
    if settings.vector_backend == "json":
        return JSONVectorIndex(target, settings.embedding_dim)
    raise ValueError(f"Unsupported VECTOR_BACKEND: {settings.vector_backend}")
