"""SQLite deduplication and response cache for VacRadar (`vacradar.scoutpipe.com`)."""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Optional


class RadarCache:
    """Lightweight SQLite key-value cache with namespace isolation and TTL expiration."""

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        ttl_hours: Optional[int] = None,
    ) -> None:
        default_db = os.getenv("CACHE_DB_PATH", "data/.cache/vacradar_cache.db")
        default_ttl = int(os.getenv("CACHE_TTL_HOURS", "168"))

        self.db_path = Path(db_path or default_db)
        self.ttl_seconds = (ttl_hours if ttl_hours is not None else default_ttl) * 3600
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError:
                pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS radar_cache (
                    namespace TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    content_hash TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (namespace, cache_key)
                )
                """
            )
            conn.commit()

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute a deterministic SHA-256 hex digest for arbitrary text content."""
        return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()

    def get(
        self,
        namespace: str,
        cache_key: str,
        expected_hash: Optional[str] = None,
    ) -> Optional[Any]:
        """Fetch a cached JSON-deserialized value if present, unexpired, and matching expected_hash."""
        now = time.time()
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                SELECT payload_json, content_hash, created_at
                FROM radar_cache
                WHERE namespace = ? AND cache_key = ?
                """,
                (namespace, cache_key),
            )
            row = cursor.fetchone()

        if not row:
            return None

        payload_json, stored_hash, created_at = row
        if self.ttl_seconds > 0 and (now - float(created_at)) > self.ttl_seconds:
            return None

        if expected_hash is not None and stored_hash != expected_hash:
            return None

        try:
            return json.loads(payload_json)
        except json.JSONDecodeError:
            return None

    def set(
        self,
        namespace: str,
        cache_key: str,
        payload: Any,
        content_hash: str = "",
    ) -> None:
        """Serialize payload to JSON and upsert into the SQLite cache."""
        payload_json = json.dumps(payload, ensure_ascii=False)
        now = time.time()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO radar_cache (namespace, cache_key, content_hash, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, cache_key) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                (namespace, cache_key, content_hash, payload_json, now),
            )
            conn.commit()

    def clear(self, namespace: Optional[str] = None) -> int:
        """Clear all entries or entries within a specific namespace."""
        with closing(self._connect()) as conn:
            if namespace:
                cursor = conn.execute("DELETE FROM radar_cache WHERE namespace = ?", (namespace,))
            else:
                cursor = conn.execute("DELETE FROM radar_cache")
            conn.commit()
            return cursor.rowcount
