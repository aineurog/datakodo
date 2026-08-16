"""Application configuration via pydantic-settings.

Settings are loaded from environment variables and .env files.
Users can override any setting at instantiation time.

``Config`` holds only cross-cutting settings. Provider-specific settings
(credentials, endpoints, rate limits) live on each adapter's own settings
object so the global config never grows a field per provider.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Global configuration for DataKodo.

    All settings can be overridden via environment variables (uppercase,
    prefixed with ``DATAKODO_``) or a ``.env`` file in the project root.
    """

    model_config = SettingsConfigDict(
        env_prefix="DATAKODO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- output ---
    output_format: str = "pandas"
    """Default output format: pandas, polars, or arrow."""

    # --- retry / backoff (design doc sec 16) ---
    max_retries: int = 3
    retry_base_delay: float = 1.0
    """Base delay in seconds for exponential backoff."""

    # --- resampling (design doc sec 7) ---
    flag_resample: bool = True
    """Emit a warning when a non-native timeframe is derived by resampling."""

    # --- logging ---
    log_level: str = "INFO"
    """Root logger level for the library."""
