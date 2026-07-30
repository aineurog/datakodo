"""MT5 raw response → canonical schema normalization.

All normalization uses vectorized operations via pandas/polars.
"""

import pandas as pd


def map_ohlcv(raw) -> pd.DataFrame:
    """Convert raw MT5 rates into a DataFrame of canonical OHLCV rows.

    MT5 CopyRates returns a numpy structured array with named columns
    'time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread',
    'real_volume'.

    Returns a DataFrame with columns matching the OHLCV schema.
    """
    if raw is None or len(raw) == 0:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "session"]
        )

    df = pd.DataFrame(raw)
    df = df.rename(
        columns={
            "time": "timestamp",
            "tick_volume": "volume",
        }
    )

    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df["session"] = "n/a"

    cols = ["timestamp", "open", "high", "low", "close", "volume", "session"]
    return df[cols]
