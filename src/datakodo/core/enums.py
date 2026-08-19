"""Core enumerations used across all adapters and schemas."""

from enum import StrEnum


class AssetClass(StrEnum):
    """Market taxonomy — the underlying asset category.

    Derivative structures such as futures, options, and CFDs are not asset
    classes; they live on ``InstrumentType``. A gold future is
    ``(METAL, FUTURE)``, an index future is ``(INDEX, FUTURE)``, and so on.
    """

    CRYPTO = "crypto"
    EQUITY = "equity"
    FOREX = "forex"
    METAL = "metal"
    BOND = "bond"
    INDEX = "index"
    ETF = "etf"


class InstrumentType(StrEnum):
    """Settlement / derivative structure of an instrument."""

    SPOT = "spot"
    PERPETUAL = "perpetual"
    FUTURE = "future"
    OPTION = "option"
    CFD = "cfd"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    MN1 = "1mo"


class Session(StrEnum):
    """Trading session for session-based assets (equity, futures).

    Crypto (24/7) and continuous forex (24/5) do not carry a session at all;
    the label only appears when a calendar actually derives it (design doc
    sec 9).
    """

    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    POST_MARKET = "post_market"
