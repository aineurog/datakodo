"""MT5 adapter settings.

Provider-specific settings live here, not on the global ``Config``. They load
from environment variables using the ``MT5_`` prefix (``MT5_TERMINAL_PATH``,
``MT5_LOGIN``, and so on) or a ``.env`` file, mirroring how the Binance adapter
owns its own settings (design doc sec 14/15).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class MT5Config(BaseSettings):
    """MetaTrader 5 adapter configuration.

    All settings load from ``MT5_``-prefixed environment variables or a
    ``.env`` file. Explicit values win over the environment. MT5 uses
    terminal-based auth — there are no API keys (design doc sec 15).
    """

    model_config = SettingsConfigDict(
        env_prefix="MT5_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    terminal_path: str = r"C:\Program Files\MetaTrader 5"
    """Path to the MT5 terminal install folder (or ``terminal64.exe`` directly).
    Defaults to the standard Windows install location."""
    login: int | None = None
    """MT5 terminal account login (int). ``None`` falls back to the default
    terminal account."""
    password: str = ""
    """MT5 terminal account password."""
    server: str = ""
    """MT5 broker server name (e.g. 'FusionMarkets-Demo')."""
    timeout: float = 10.0
    """Timeout in seconds for MT5 operations (initialization and data calls)."""
    rate_limit_rate: float = 5.0
    """MT5 token-bucket refill rate (tokens/sec). MT5 is a local, non-weighted
    terminal, so this is a conservative throttle on data requests."""
    rate_limit_burst: int = 10
    """MT5 token-bucket burst capacity."""
    max_bars: int = 1000
    """Bar cap per ``copy_rates_range`` request. MT5 returns at most what the
    symbol's chart has loaded ('Max. bars in chart'); wide ranges are chunked
    into ``max_bars`` slices and stitched by ``ops.pagination.paginate``
    (design doc sec 12). Lower the default only if your terminal loads fewer
    bars than this."""
    market_type: str = "forex"
    """Default MT5 market classification: 'forex', 'cfd', 'index', or 'metal'.
    Used to select the canonical ``Instrument`` type when no symbol-specific
    classification is available."""
