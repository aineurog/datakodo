"""Canonical-to-provider timeframe mapping.

Each adapter maps canonical timeframes (1m, 5m, 1h, ...) to its own
provider-specific string format.
"""

from datakodo.core.enums import Timeframe

# Canonical -> Binance
BINANCE_MAP: dict[Timeframe, str] = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
    Timeframe.W1: "1w",
    Timeframe.MN1: "1M",
}

# Canonical -> MT5 (MetaTrader 5 constants)
MT5_MAP: dict[Timeframe, int] = {
    Timeframe.M1: 1,     # TIMEFRAME_M1
    Timeframe.M5: 5,     # TIMEFRAME_M5
    Timeframe.M15: 15,   # TIMEFRAME_M15
    Timeframe.M30: 30,   # TIMEFRAME_M30
    Timeframe.H1: 16385, # TIMEFRAME_H1
    Timeframe.H4: 16388, # TIMEFRAME_H4
    Timeframe.D1: 16408, # TIMEFRAME_D1
    Timeframe.W1: 32769, # TIMEFRAME_W1
    Timeframe.MN1: 49153,# TIMEFRAME_MN1
}

# Canonical -> IBKR duration strings
IBKR_MAP: dict[Timeframe, str] = {
    Timeframe.M1: "1 min",
    Timeframe.M5: "5 mins",
    Timeframe.M15: "15 mins",
    Timeframe.M30: "30 mins",
    Timeframe.H1: "1 hour",
    Timeframe.H4: "4 hours",
    Timeframe.D1: "1 day",
    Timeframe.W1: "1 week",
    Timeframe.MN1: "1 month",
}
