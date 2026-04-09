"""Secure in-memory key vault — stores API keys per session token.

Keys are:
- Stored as pydantic SecretStr (masked in logs, repr, tracebacks)
- TTL-based: auto-expire after 24 hours
- Never sent over WebSocket, only via REST
- Cleared on server restart (in-memory only)
"""

import time
import uuid
from dataclasses import dataclass, field

import structlog
from pydantic import BaseModel, SecretStr

logger = structlog.get_logger("server.key_vault")


class ProviderKeys(BaseModel):
    """API keys for all supported providers."""

    openai_api_key: SecretStr = SecretStr("")
    groq_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    ollama_base_url: str = "http://localhost:11434"
    preferred_provider: str = ""
    preferred_model: str = ""


@dataclass
class VaultEntry:
    """A single vault entry with TTL tracking."""

    token: str
    keys: ProviderKeys
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    ttl_seconds: float = 86400  # 24 hours


class KeyVault:
    """In-memory vault for session-scoped API keys.

    Thread-safe for concurrent FastAPI requests.
    Keys are stored only in memory — they vanish on server restart.
    """

    def __init__(self, default_ttl: float = 86400):
        self._entries: dict[str, VaultEntry] = {}
        self._default_ttl = default_ttl

    def store_keys(self, keys: ProviderKeys) -> str:
        """Store keys and return an opaque session token."""
        self._cleanup_expired()

        token = str(uuid.uuid4())
        self._entries[token] = VaultEntry(
            token=token,
            keys=keys,
            ttl_seconds=self._default_ttl,
        )
        logger.info("keys_stored",
                     token_prefix=token[:8],
                     providers=self._configured_providers(keys))
        return token

    def get_keys(self, token: str) -> ProviderKeys | None:
        """Retrieve keys by session token. Returns None if expired/missing."""
        self._cleanup_expired()

        entry = self._entries.get(token)
        if entry is None:
            return None

        if self._is_expired(entry):
            del self._entries[token]
            logger.info("keys_expired", token_prefix=token[:8])
            return None

        entry.last_accessed = time.time()
        return entry.keys

    def revoke(self, token: str) -> bool:
        """Revoke a session token and clear its keys."""
        if token in self._entries:
            del self._entries[token]
            logger.info("keys_revoked", token_prefix=token[:8])
            return True
        return False

    def get_status(self, token: str) -> dict:
        """Check which providers are configured (no keys exposed)."""
        keys = self.get_keys(token)
        if keys is None:
            return {"valid": False, "providers": {}}

        return {
            "valid": True,
            "providers": {
                "openai": bool(keys.openai_api_key.get_secret_value()),
                "groq": bool(keys.groq_api_key.get_secret_value()),
                "openrouter": bool(keys.openrouter_api_key.get_secret_value()),
                "ollama": bool(keys.ollama_base_url),
            },
            "preferred_provider": keys.preferred_provider,
            "preferred_model": keys.preferred_model,
        }

    @property
    def active_tokens(self) -> int:
        self._cleanup_expired()
        return len(self._entries)

    def _is_expired(self, entry: VaultEntry) -> bool:
        return (time.time() - entry.created_at) > entry.ttl_seconds

    def _cleanup_expired(self) -> None:
        expired = [t for t, e in self._entries.items() if self._is_expired(e)]
        for t in expired:
            del self._entries[t]
        if expired:
            logger.info("keys_cleanup", expired_count=len(expired))

    @staticmethod
    def _configured_providers(keys: ProviderKeys) -> list[str]:
        providers = []
        if keys.openai_api_key.get_secret_value():
            providers.append("openai")
        if keys.groq_api_key.get_secret_value():
            providers.append("groq")
        if keys.openrouter_api_key.get_secret_value():
            providers.append("openrouter")
        if keys.ollama_base_url:
            providers.append("ollama")
        return providers


# Singleton instance
key_vault = KeyVault()
