"""Application configuration via pydantic-settings.

Settings are loaded from environment variables and .env files.
Users can override any setting at instantiation time.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Global configuration for DataKodo.

    All settings can be overridden via environment variables (uppercase)
    or a ``.env`` file in the project root.
    """

    model_config = SettingsConfigDict(
        env_prefix="DATAKODO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- output ---
    output_format: str = "pandas"
    """Default output format: pandas, polars, arrow, or numpy."""

    # --- storage / cache ---
    cache_enabled: bool = True
    cache_dir: Path = Path("datakodo_cache")
    """Directory for the local Parquet cache."""

    # --- rate limiting ---
    max_retries: int = 3
    retry_base_delay: float = 1.0
    """Base delay in seconds for exponential backoff."""

    # --- display ---
    display_timezone: str = "UTC"
    """Timezone for display purposes. Storage is always UTC."""
