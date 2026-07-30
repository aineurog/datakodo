"""Core enumerations used across all adapters and schemas."""

from enum import StrEnum


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    EQUITY = "equity"
    FOREX = "forex"
    FUTURE = "future"
    OPTION = "option"
    METAL = "metal"
    BOND = "bond"
    CFD = "cfd"


class InstrumentType(StrEnum):
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
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    POST_MARKET = "post_market"
    NA = "n/a"
