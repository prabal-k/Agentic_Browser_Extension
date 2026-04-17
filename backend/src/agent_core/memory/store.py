"""SQLite-backed persistent memory store.

Three tables:
- site_knowledge: domain-specific patterns and hints (e.g. "React forms need nativeSetter")
- task_history: past task outcomes per domain
- action_stats: aggregated action success/fail rates per domain + action type
"""

import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger("memory.store")

# Categories for site_knowledge
CATEGORY_FORM_PATTERN = "form_pattern"       # How forms work on this site
CATEGORY_NAV_PATTERN = "nav_pattern"         # Navigation quirks
CATEGORY_LOGIN_FLOW = "login_flow"           # Login-specific knowledge
CATEGORY_ELEMENT_HINT = "element_hint"       # Which elements to prefer/avoid
CATEGORY_ACTION_HINT = "action_hint"         # Action-specific tips
CATEGORY_GENERAL = "general"                 # Misc site knowledge

_SCHEMA = """
CREATE TABLE IF NOT EXISTS site_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    hit_count INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(domain, category, key)
);

CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    domain TEXT DEFAULT '',
    success INTEGER NOT NULL DEFAULT 0,
    total_actions INTEGER DEFAULT 0,
    duration_seconds REAL DEFAULT 0,
    summary TEXT DEFAULT '',
    failure_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    action_type TEXT NOT NULL,
    context TEXT DEFAULT '',
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    last_used TEXT NOT NULL,
    notes TEXT DEFAULT '',
    UNIQUE(domain, action_type, context)
);

CREATE INDEX IF NOT EXISTS idx_site_domain ON site_knowledge(domain);
CREATE INDEX IF NOT EXISTS idx_task_domain ON task_history(domain);
CREATE INDEX IF NOT EXISTS idx_task_created ON task_history(created_at);
CREATE INDEX IF NOT EXISTS idx_action_domain ON action_stats(domain);
"""


def extract_domain(url: str) -> str:
    """Extract domain from URL, stripping www. prefix."""
    if not url:
        return ""
    # Skip non-http schemes (about:blank, chrome://, data:, etc.)
    if url.startswith(("about:", "data:", "blob:", "javascript:")):
        return ""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        domain = parsed.hostname or ""
        if domain.startswith("www."):
            domain = domain[4:]
        return domain.lower()
    except Exception:
        return ""


class PersistentMemory:
    """SQLite-backed memory that persists across agent sessions."""

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        logger.info("memory_initialized", db_path=str(self._db_path))

    def close(self):
        self._conn.close()

    # ------------------------------------------------------------------
    # Site Knowledge
    # ------------------------------------------------------------------

    def get_site_knowledge(self, domain: str) -> list[dict]:
        """Get all knowledge entries for a domain, ordered by relevance."""
        rows = self._conn.execute(
            "SELECT category, key, value, confidence, hit_count "
            "FROM site_knowledge WHERE domain = ? "
            "ORDER BY hit_count DESC, confidence DESC",
            (domain,),
        ).fetchall()
        return [dict(r) for r in rows]

    def save_site_knowledge(
        self,
        domain: str,
        category: str,
        key: str,
        value: str,
        confidence: float = 0.5,
    ) -> None:
        """Save or update a site knowledge entry."""
        now = _now()
        self._conn.execute(
            "INSERT INTO site_knowledge (domain, category, key, value, confidence, hit_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(domain, category, key) DO UPDATE SET "
            "value = excluded.value, confidence = excluded.confidence, "
            "hit_count = hit_count + 1, updated_at = excluded.updated_at",
            (domain, category, key, value, confidence, now, now),
        )
        self._conn.commit()

    def boost_knowledge(self, domain: str, category: str, key: str) -> None:
        """Increment hit_count for a knowledge entry (confirms it's useful)."""
        self._conn.execute(
            "UPDATE site_knowledge SET hit_count = hit_count + 1, updated_at = ? "
            "WHERE domain = ? AND category = ? AND key = ?",
            (_now(), domain, category, key),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Task History
    # ------------------------------------------------------------------

    def save_task(
        self,
        session_id: str,
        goal: str,
        domain: str = "",
        success: bool = False,
        total_actions: int = 0,
        duration_seconds: float = 0,
        summary: str = "",
        failure_reason: str = "",
    ) -> int:
        """Save a completed task. Returns the row ID."""
        cur = self._conn.execute(
            "INSERT INTO task_history "
            "(session_id, goal, domain, success, total_actions, duration_seconds, summary, failure_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, goal, domain, int(success), total_actions,
             duration_seconds, summary, failure_reason, _now()),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_recent_tasks(self, domain: str | None = None, limit: int = 10) -> list[dict]:
        """Get recent tasks, optionally filtered by domain."""
        if domain:
            rows = self._conn.execute(
                "SELECT * FROM task_history WHERE domain = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (domain, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM task_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_domain_success_rate(self, domain: str) -> dict:
        """Get success rate for a domain. Returns {total, successes, rate}."""
        row = self._conn.execute(
            "SELECT COUNT(*) as total, SUM(success) as successes "
            "FROM task_history WHERE domain = ?",
            (domain,),
        ).fetchone()
        total = row["total"] or 0
        successes = row["successes"] or 0
        return {
            "total": total,
            "successes": successes,
            "rate": successes / total if total > 0 else 0.0,
        }

    # ------------------------------------------------------------------
    # Action Stats
    # ------------------------------------------------------------------

    def record_action(
        self,
        domain: str,
        action_type: str,
        success: bool,
        context: str = "",
        notes: str = "",
    ) -> None:
        """Record an action outcome. Updates running success/fail counts."""
        now = _now()
        if success:
            self._conn.execute(
                "INSERT INTO action_stats (domain, action_type, context, success_count, fail_count, last_used, notes) "
                "VALUES (?, ?, ?, 1, 0, ?, ?) "
                "ON CONFLICT(domain, action_type, context) DO UPDATE SET "
                "success_count = success_count + 1, last_used = excluded.last_used",
                (domain, action_type, context, now, notes),
            )
        else:
            self._conn.execute(
                "INSERT INTO action_stats (domain, action_type, context, success_count, fail_count, last_used, notes) "
                "VALUES (?, ?, ?, 0, 1, ?, ?) "
                "ON CONFLICT(domain, action_type, context) DO UPDATE SET "
                "fail_count = fail_count + 1, last_used = excluded.last_used, "
                "notes = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE notes END",
                (domain, action_type, context, now, notes),
            )
        self._conn.commit()

    def get_action_stats(self, domain: str) -> list[dict]:
        """Get action stats for a domain."""
        rows = self._conn.execute(
            "SELECT action_type, context, success_count, fail_count, notes "
            "FROM action_stats WHERE domain = ? "
            "ORDER BY (fail_count * 1.0 / MAX(success_count + fail_count, 1)) DESC",
            (domain,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Prompt Formatting
    # ------------------------------------------------------------------

    def format_for_prompt(self, domain: str, max_lines: int = 12) -> str:
        """Format memory for injection into LLM prompts.

        Returns a concise text block with site knowledge, past task results,
        and action reliability hints -ready to paste into a prompt.
        """
        if not domain:
            return ""

        parts = []

        # 1. Site knowledge
        knowledge = self.get_site_knowledge(domain)
        if knowledge:
            hints = []
            for k in knowledge[:8]:
                hints.append(f"- [{k['category']}] {k['value']}")
            parts.append("Site knowledge:\n" + "\n".join(hints))

        # 2. Recent task history on this domain
        tasks = self.get_recent_tasks(domain, limit=3)
        if tasks:
            task_lines = []
            for t in tasks:
                status = "OK" if t["success"] else "FAILED"
                reason = f" ({t['failure_reason'][:60]})" if t.get("failure_reason") else ""
                task_lines.append(
                    f"- [{status}] \"{t['goal'][:80]}\" - {t['total_actions']} actions{reason}"
                )
            parts.append("Past tasks on this site:\n" + "\n".join(task_lines))

        # 3. Unreliable actions
        stats = self.get_action_stats(domain)
        unreliable = [
            s for s in stats
            if s["fail_count"] > 0 and s["fail_count"] / max(s["success_count"] + s["fail_count"], 1) > 0.3
        ]
        if unreliable:
            warn_lines = []
            for s in unreliable[:4]:
                total = s["success_count"] + s["fail_count"]
                note = f" - {s['notes']}" if s.get("notes") else ""
                warn_lines.append(
                    f"- {s['action_type']} (context: {s['context'] or 'any'}): "
                    f"{s['fail_count']}/{total} failures{note}"
                )
            parts.append("Unreliable actions on this site:\n" + "\n".join(warn_lines))

        if not parts:
            return ""

        result = "\n\n".join(parts)
        # Truncate to max_lines
        lines = result.split("\n")
        if len(lines) > max_lines:
            result = "\n".join(lines[:max_lines]) + "\n..."
        return result

    # ------------------------------------------------------------------
    # Auto-learn from task completion
    # ------------------------------------------------------------------

    def learn_from_task(
        self,
        domain: str,
        success: bool,
        action_history: list[dict],
        failure_reason: str = "",
    ) -> None:
        """Extract patterns from a completed task and save as site knowledge.

        Called after task finalization. Analyzes action history to find
        patterns worth remembering.
        """
        if not domain or not action_history:
            return

        # Pattern: repeated failures on specific action types
        action_fails: dict[str, int] = {}
        for entry in action_history:
            result = entry.get("result", {})
            action = entry.get("action", {})
            atype = action.get("action_type", "")
            if isinstance(atype, str):
                atype_str = atype
            else:
                atype_str = atype.value if hasattr(atype, "value") else str(atype)

            status = result.get("status", "")
            if isinstance(status, str):
                status_str = status
            else:
                status_str = status.value if hasattr(status, "value") else str(status)

            if status_str in ("failed", "element_not_found", "timeout"):
                action_fails[atype_str] = action_fails.get(atype_str, 0) + 1

        for atype_str, count in action_fails.items():
            if count >= 2:
                self.save_site_knowledge(
                    domain,
                    CATEGORY_ACTION_HINT,
                    f"{atype_str}_unreliable",
                    f"{atype_str} failed {count} times - try alternative approaches",
                    confidence=0.6,
                )

        # Pattern: successful task with few actions = efficient path found
        if success and len(action_history) <= 5:
            action_types = []
            for entry in action_history:
                action = entry.get("action", {})
                atype = action.get("action_type", "")
                desc = action.get("description", "")
                if isinstance(atype, str):
                    action_types.append(f"{atype}: {desc[:50]}")
                elif hasattr(atype, "value"):
                    action_types.append(f"{atype.value}: {desc[:50]}")
            if action_types:
                self.save_site_knowledge(
                    domain,
                    CATEGORY_GENERAL,
                    "efficient_path",
                    f"Efficient action sequence: {' -> '.join(action_types)}",
                    confidence=0.7,
                )

        # Pattern: if login was involved and succeeded
        login_actions = [
            e for e in action_history
            if any(kw in (e.get("action", {}).get("description", "") or "").lower()
                   for kw in ("login", "sign in", "password", "email"))
        ]
        if login_actions and success:
            self.save_site_knowledge(
                domain,
                CATEGORY_LOGIN_FLOW,
                "login_works",
                "Login flow works on this site - standard email/password form",
                confidence=0.8,
            )

        logger.info("memory_learned", domain=domain, success=success,
                     patterns_checked=len(action_fails))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------
# Singleton access
# ------------------------------------------------------------------

_instance: PersistentMemory | None = None


def get_memory() -> PersistentMemory:
    """Get or create the singleton PersistentMemory instance."""
    global _instance
    if _instance is None:
        from agent_core.config import settings
        db_dir = getattr(settings, "memory_dir", "")
        if not db_dir:
            db_dir = str(Path(__file__).parent.parent.parent.parent / "data")
        db_path = Path(db_dir) / "agent_memory.db"
        _instance = PersistentMemory(db_path)
    return _instance
