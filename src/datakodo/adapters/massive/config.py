"""Massive adapter settings.

Provider-specific settings live here, not on the global ``Config``. They load
from environment variables using Massive's standard name (``MASSIVE_API_KEY``)
so users can reuse an existing environment.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class MassiveConfig(BaseSettings):
    """Massive.com adapter configuration (design doc sec 14/15).

    All settings load from ``MASSIVE_``-prefixed environment variables or a
    ``.env`` file. Explicit values win over the environment.
    """

    model_config = SettingsConfigDict(
        env_prefix="MASSIVE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = ""
    """Massive API key. Public market data always needs a key (unlike Binance);

    validated lazily - raised only when a call actually needs it."""
    base_url: str = "https://api.massive.com"
    """Massive REST base URL (rebranded from api.polygon.io on 2025-10-30)."""
    timeout: float = 10.0
    """Per-request connect/read timeout in seconds for Massive REST."""
    rate_limit_rate: float = 0.1
    """Massive token-bucket refill rate (tokens/sec); ~6/min matches the free tier."""
    rate_limit_burst: int = 5
    """Massive token-bucket burst capacity."""
