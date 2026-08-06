"""MT5 raw response → canonical schema normalization.

All normalization uses vectorized operations via pandas/polars.
"""

import pandas as pd

from datakodo.core.exceptions import ProviderError

# MT5 CopyRates volume baselines. MT5 returns both ``tick_volume`` and
# ``real_volume``. For forex/CFD instruments ``real_volume`` is usually 0
# because brokers do not report true traded volume, whereas ``tick_volume``
# counts every price-change tick and is reliably populated. ``tick_volume``
# is therefore the default; the choice is exposed as an explicit parameter.
VOLUME_BASELINES = ("tick_volume", "real_volume")


def map_ohlcv(raw, volume: str = "tick_volume", offset_seconds: int = 0) -> pd.DataFrame:
    """Convert raw MT5 rates into a DataFrame of canonical OHLCV rows.

    MT5 CopyRates returns a numpy structured array with named columns
    'time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread',
    'real_volume'. Raw ``time`` values are in **server time**; subtracting
    ``offset_seconds`` (see ``MT5Terminal.server_offset_seconds``) before
    ``utc=True`` yields true UTC.

    ``volume`` selects the canonical ``volume`` baseline:
    - ``"tick_volume"`` (default) — reliable for forex/CFDs.
    - ``"real_volume"`` — broker-traded volume (often 0 for forex).

    Returns a DataFrame with columns matching the OHLCV schema.
    """
    if volume not in VOLUME_BASELINES:
        raise ProviderError(
            f"Invalid volume baseline {volume!r}; expected one of {VOLUME_BASELINES}"
        )

    if raw is None or len(raw) == 0:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "session"]
        )

    df = pd.DataFrame(raw)
    df = df.rename(
        columns={
            "time": "timestamp",
            volume: "volume",
        }
    )

    # Server time → true UTC epoch, then to UTC-aware timestamps.
    df["timestamp"] = pd.to_datetime(df["timestamp"] - offset_seconds, unit="s", utc=True)
    df["session"] = "n/a"

    cols = ["timestamp", "open", "high", "low", "close", "volume", "session"]
    return df[cols]
