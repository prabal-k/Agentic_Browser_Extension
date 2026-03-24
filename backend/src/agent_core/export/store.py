"""In-memory store for exportable data with TTL expiry."""

import time
import uuid


class ExportStore:
    """Holds export-ready data keyed by export_id. Auto-expires after TTL."""

    def __init__(self, ttl_seconds: int = 600):
        self._data: dict[str, dict] = {}
        self._ttl = ttl_seconds

    def store(self, session_id: str, data: list[dict] | dict, metadata: dict) -> str:
        """Store exportable data and return an export_id."""
        self._cleanup()
        export_id = uuid.uuid4().hex[:12]
        self._data[export_id] = {
            "session_id": session_id,
            "data": data,
            "metadata": metadata,
            "created_at": time.time(),
        }
        return export_id

    def get(self, export_id: str) -> dict | None:
        """Retrieve stored data. Returns None if expired or not found."""
        self._cleanup()
        return self._data.get(export_id)

    def _cleanup(self):
        """Remove entries older than TTL."""
        now = time.time()
        expired = [k for k, v in self._data.items() if now - v["created_at"] > self._ttl]
        for k in expired:
            del self._data[k]


# Singleton
export_store = ExportStore()
