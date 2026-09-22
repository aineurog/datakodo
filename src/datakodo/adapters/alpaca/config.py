"""Alpaca adapter settings.

Provider-specific settings live here, not on the global ``Config``. They load
from environment variables using Alpaca's standard names
(``APCA_API_KEY_ID``, ``APCA_API_SECRET_KEY``) so users can reuse an existing
environment. The ``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY`` spellings used by
some Alpaca tooling are accepted as aliases; when both spellings are present
the ``APCA_*`` spelling wins.
"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AlpacaConfig(BaseSettings):
    """Alpaca Markets adapter configuration (design doc sec 14/15).

    All settings load from environment variables or a ``.env`` file.
    Explicit values win over the environment. Auth is validated lazily —
    an ``AuthenticationError`` is raised only when a call actually needs a
    key that is missing, never at import or construction time.
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
    """Alpaca API key ID. Public market data reads need a key (unlike Binance).

    Validated lazily - raised only when a call actually needs it."""

    api_secret: str = Field(
        default="",
        validation_alias=AliasChoices("APCA_API_SECRET_KEY", "ALPACA_SECRET_KEY"),
    )
    """Alpaca API secret key. Validated lazily, like ``api_key``."""

    data_base_url: str = "https://data.alpaca.markets"
    """Alpaca Market Data REST base URL (bars, trades, quotes, snapshots).

    Data fetching needs this host only. Trading-host URLs (assets, clock,
    calendar) are a step 5 concern and arrive there with evidence, if at all."""

    feed: str = "iex"
    """Default market-data feed. ``iex`` is the only feed available without a
    paid subscription; ``sip`` requires a paid data plan."""

    rate_limit_rate: float = 3.0
    """Alpaca token-bucket refill rate (tokens/sec).

    Approximate starting point for the free tier; the transport reacts to
    ``X-RateLimit-*`` response headers rather than trusting this number."""

    rate_limit_burst: int = 10
    """Alpaca token-bucket burst capacity."""
