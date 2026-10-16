"""Runtime settings, read from AGENTSCOPE_* environment variables.

Defaults are local-first: loopback binding, a SQLite file under ./.agentscope, no OTLP
export, no external model hosts. Settings are the only source of network destinations
(see providers/http.py), and no `.env` file is ever loaded implicitly: a project-local
file could otherwise redirect a base URL while the API key comes from the user's shell.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class ModelRate(BaseSettings):
    """Explicit price per 1K tokens for one model. Costs are only shown when configured."""

    input_per_1k_tokens: float = Field(ge=0)
    output_per_1k_tokens: float = Field(ge=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTSCOPE_", env_nested_delimiter="__", extra="ignore", env_file=None
    )

    data_dir: Path = Path(".agentscope")
    database_url: str | None = None

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    allow_remote: bool = False
    # Extra Origin values allowed to make state-changing API requests (e.g. a Vite dev server).
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )
    max_request_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)

    # Ollama is local, so it is selectable by default; it is contacted only when a run selects
    # provider=ollama or the provider status is probed.
    enable_ollama: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    # Non-loopback hostnames (or IP addresses) that may be contacted: model providers and the
    # OTLP endpoint. Exact matches only. Empty = loopback only.
    allowed_egress_hosts: list[str] = Field(default_factory=list)
    # Earlier name for allowed_egress_hosts; still honoured.
    allowed_model_hosts: list[str] = Field(default_factory=list)
    # Sends prompts and tool outputs to the configured server: off unless enabled explicitly.
    enable_openai_compatible: bool = False
    openai_compatible_url: str | None = None  # base URL, e.g. http://127.0.0.1:8080/v1
    openai_compatible_api_key: SecretStr | None = Field(default=None, repr=False)

    # Optional OpenTelemetry export (OTLP/HTTP). Off unless explicitly configured.
    otlp_endpoint: str | None = None

    # Explicit model pricing, e.g. AGENTSCOPE_MODEL_RATES='{"llama3.2": {...}}'.
    model_rates: dict[str, ModelRate] = Field(default_factory=dict)

    # Additional attribute keys to redact from traces before persistence.
    redact_keys: list[str] = Field(default_factory=list)

    @field_validator("data_dir")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'agentscope.db').resolve()}"

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
