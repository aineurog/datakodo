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

    # --- binance adapter (design doc sec 13/14: single user-editable .env) ---
    binance_api_key: str = ""
    """Binance API key. Public market data needs no key."""
    binance_api_secret: str = ""
    """Binance API secret. Public market data needs no secret."""
    binance_testnet: bool = False
    """Use the Binance testnet (Spot/Futures test endpoints) when true."""
    binance_tld: str = "com"
    """Binance top-level domain: 'com', 'us', 'jp', etc."""
    binance_market_type: str = "spot"
    """Default Binance market: 'spot' or 'futures'."""
    binance_timeout: float = 10.0
    """Per-request timeout in seconds for Binance REST and WebSockets."""
    binance_rate_limit_rate: float = 100.0
    """Binance token-bucket refill rate (tokens/sec); 100/s matches spot."""
    binance_rate_limit_burst: int = 1000
    """Binance token-bucket burst capacity."""
