"""Binance adapter settings.

Provider-specific settings live here, not on the global ``Config``. They load
from environment variables using Binance's standard names (``BINANCE_API_KEY``,
``BINANCE_API_SECRET``, and so on) so users can reuse an existing environment.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class BinanceConfig(BaseSettings):
    """Binance adapter configuration.

    All settings load from ``BINANCE_``-prefixed environment variables or a
    ``.env`` file. Explicit values win over the environment.
    """

    model_config = SettingsConfigDict(
        env_prefix="BINANCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = ""
    """Binance API key. Public market data needs no key."""
    api_secret: str = ""
    """Binance API secret. Public market data needs no secret."""
    testnet: bool = False
    """Use the Binance testnet (Spot/Futures test endpoints) when true."""
    tld: str = "com"
    """Binance top-level domain: 'com', 'us', 'jp', and so on."""
    market_type: str = "spot"
    """Default Binance market: 'spot' or 'futures'."""
    timeout: float = 10.0
    """Per-request timeout in seconds for Binance REST and WebSockets."""
    rate_limit_rate: float = 100.0
    """Binance token-bucket refill rate (tokens/sec); 100/s matches spot."""
    rate_limit_burst: int = 1000
    """Binance token-bucket burst capacity."""
