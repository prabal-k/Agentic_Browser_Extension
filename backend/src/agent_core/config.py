"""Application configuration using pydantic-settings.

Loads from environment variables and .env file.

Security:
- Secrets (API keys, server URLs) are loaded from .env only
- .env is git-ignored — never committed
- The Settings __repr__ masks sensitive fields
- No secrets are ever logged or printed in full
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field, SecretStr
from dotenv import load_dotenv


# Find the .env file relative to this file's location
# This works whether you run from backend/ or from project root
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _BACKEND_DIR / ".env"

# Load ALL .env vars into os.environ so non-AGENT_ vars
# (like LANGCHAIN_*) are available to libraries that read os.environ directly.
# override=True ensures .env values are set even in uvicorn worker processes.
load_dotenv(_ENV_FILE, override=True)


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All secrets use SecretStr to prevent accidental logging/printing.
    Access the actual value with: settings.openai_api_key.get_secret_value()
    """

    # Ollama configuration
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama server URL"
    )
    ollama_model: str = Field(
        default="qwen2.5:32b-instruct",
        description="Default Ollama model name"
    )
    fast_model: str = Field(
        default="",
        description="Fast model for simple actions (navigate, click, type). Empty = use main model for everything."
    )
    vision_model: str = Field(
        default="",
        description="Vision model for screenshot analysis (e.g., qwen3-vl:8b). Empty = vision disabled."
    )

    # OpenAI configuration (fallback)
    # SecretStr prevents the key from appearing in logs, repr, or tracebacks
    openai_api_key: SecretStr = Field(
        default="",
        description="OpenAI API key for fallback model"
    )
    openai_model: str = Field(
        default="gpt-4o",
        description="Default OpenAI model name"
    )

    # Groq configuration
    groq_api_key: SecretStr = Field(
        default="",
        description="Groq API key"
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Default Groq model name"
    )

    # OpenRouter configuration (OpenAI-compatible proxy for 300+ models)
    openrouter_api_key: SecretStr = Field(
        default="",
        description="OpenRouter API key (sk-or-v1-...)"
    )
    openrouter_model: str = Field(
        default="meta-llama/llama-3.3-70b-instruct:free",
        description="Default OpenRouter model name"
    )

    # Agent configuration
    max_iterations: int = Field(
        default=25,
        description="Maximum reasoning loop iterations before forced stop"
    )
    confidence_threshold: float = Field(
        default=0.6,
        description="Below this confidence, always ask for user confirmation"
    )
    auto_confirm: bool = Field(
        default=False,
        description="Skip confirmation for low-risk, high-confidence actions"
    )
    enable_evaluate_js: bool = Field(
        default=False,
        description=(
            "Allow the agent to call evaluate_js (arbitrary JavaScript in page "
            "context). Disabled by default — only enable in trusted development "
            "environments. Must NEVER be True in public/store builds because a "
            "spoofed or leaked session token would let an attacker run arbitrary "
            "JS in any tab the user opens."
        ),
    )

    # Server configuration
    server_host: str = Field(default="0.0.0.0")
    server_port: int = Field(default=8000)
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        description="Allowed CORS origins"
    )

    # Memory / persistence
    memory_dir: str = Field(
        default="",
        description="Directory for persistent memory DB. Empty = backend/data/"
    )

    # Logging
    log_level: str = Field(default="INFO")
    log_format: str = Field(
        default="console",
        description="Log format: 'json' for production, 'console' for development"
    )

    model_config = {
        "env_file": str(_ENV_FILE),
        "env_prefix": "AGENT_",
        "case_sensitive": False,
        "extra": "ignore",  # Ignore non-AGENT_ vars like LANGCHAIN_* in .env
    }

    def display_config(self) -> str:
        """Safe string representation that masks secrets."""
        def _mask(key: SecretStr) -> str:
            val = key.get_secret_value()
            return f"{val[:4]}...{val[-4:]}" if len(val) > 8 else "not set"

        return (
            f"Settings:\n"
            f"  Ollama URL:    {self.ollama_base_url}\n"
            f"  Ollama Model:  {self.ollama_model}\n"
            f"  OpenAI Key:    {_mask(self.openai_api_key)}\n"
            f"  OpenAI Model:  {self.openai_model}\n"
            f"  Groq Key:      {_mask(self.groq_api_key)}\n"
            f"  Groq Model:    {self.groq_model}\n"
            f"  OpenRouter Key: {_mask(self.openrouter_api_key)}\n"
            f"  OpenRouter Model: {self.openrouter_model}\n"
            f"  Max Iterations: {self.max_iterations}\n"
            f"  Auto Confirm:  {self.auto_confirm}\n"
            f"  Log Level:     {self.log_level}\n"
        )


# Singleton instance
settings = Settings()
