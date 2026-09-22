"""Alpaca adapter settings (design doc sec 14/15).

Provider-specific settings live here, not on the global ``Config``. Keys load
from ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY``, with the ``ALPACA_API_KEY``
/ ``ALPACA_SECRET_KEY`` spellings as aliases (``APCA_*`` wins).
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AlpacaConfig(BaseSettings):
    """Alpaca Markets adapter configuration.

    Explicit values win over the environment. Auth is validated lazily —
    only when a call actually needs a missing key.
    """

    model_config = SettingsConfigDict(
        env_prefix="APCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("APCA_API_KEY_ID", "ALPACA_API_KEY"),
    )
    """Alpaca API key ID. Validated lazily, when a call needs it."""

    api_secret: str = Field(
        default="",
        validation_alias=AliasChoices("APCA_API_SECRET_KEY", "ALPACA_SECRET_KEY"),
    )
    """Alpaca API secret key. Validated lazily, like ``api_key``."""

    data_base_url: str = "https://data.alpaca.markets"
    """Market Data REST base URL (bars, trades, quotes, snapshots)."""

    feed: str = "iex"
    """Default market-data feed. ``iex`` is the only feed available without a
    paid subscription; ``sip`` requires a paid data plan."""

    rate_limit_rate: float = 3.0
    """Token-bucket refill rate (tokens/sec). Headers rule at runtime."""

    rate_limit_burst: int = 10
    """Alpaca token-bucket burst capacity."""
