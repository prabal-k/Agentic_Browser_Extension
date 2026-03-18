"""Structured logging configuration using structlog.

Provides structured JSON logs for production and
colorized console logs for development.

Security: All log events pass through a key-redaction processor
that masks any value matching API key patterns.
"""

import re

import structlog
from agent_core.config import settings

# Patterns that look like API keys — redact them from all log output
_KEY_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}"),        # OpenAI
    re.compile(r"gsk_[a-zA-Z0-9_-]{20,}"),        # Groq
    re.compile(r"lsv2_pt_[a-zA-Z0-9_-]{20,}"),    # LangSmith
]

_SENSITIVE_FIELD_NAMES = {"api_key", "api_keys", "secret", "token", "password", "authorization"}


def _redact_keys(logger, method_name, event_dict):
    """Structlog processor that redacts API key patterns from all log values."""
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            # Redact known key patterns
            for pattern in _KEY_PATTERNS:
                value = pattern.sub("[REDACTED]", value)
            event_dict[key] = value
        # Redact fields with sensitive names
        if key.lower() in _SENSITIVE_FIELD_NAMES and isinstance(value, str):
            event_dict[key] = "[REDACTED]"
    return event_dict


def setup_logging() -> None:
    """Configure structlog based on application settings."""

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        _redact_keys,
    ]

    if settings.log_format == "console":
        # Development: colorized, human-readable output
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                structlog.stdlib.NAME_TO_LEVEL[settings.log_level.lower()]
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Production: JSON output
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                structlog.stdlib.NAME_TO_LEVEL[settings.log_level.lower()]
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a named logger instance."""
    return structlog.get_logger(name)
