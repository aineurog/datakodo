"""Auto-paginate and stitch — transparently fetch large date ranges.

Providers cap results per request (e.g. 1000 candles per call). This
module chops a wide date range into provider-size chunks, fetches each
chunk, and stitches the results together so users never manage this
manually.
"""

import logging
from datetime import datetime, timedelta

import pandas as pd

from datakodo.core.enums import Timeframe

logger = logging.getLogger(__name__)

_TIMEFRAME_DELTA: dict[Timeframe, timedelta] = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M30: timedelta(minutes=30),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
    Timeframe.W1: timedelta(weeks=1),
    Timeframe.MN1: timedelta(days=30),
}


def paginate(
    fetch_fn,
    symbol: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    *,
    max_per_request: int = 1000,
) -> pd.DataFrame:
    """Paginate *fetch_fn* across *start* → *end* in *max_per_request* slices.

    *fetch_fn* must have the signature:
        fetch_fn(symbol: str, start: datetime, end: datetime) -> pd.DataFrame

    Results from each slice are concatenated and deduplicated on
    *timestamp* before being returned.
    """
    if start >= end:
        raise ValueError(f"start ({start}) must be before end ({end}).")

    delta = _TIMEFRAME_DELTA.get(timeframe)
    if delta is None:
        raise ValueError(f"Unknown timeframe: {timeframe}")

    chunk_delta = delta * max_per_request
    chunks: list[pd.DataFrame] = []
    cursor = start

    while cursor < end:
        chunk_end = min(cursor + chunk_delta, end)
        logger.debug("Fetching %s [%s → %s]", symbol, cursor, chunk_end)

        df_chunk = fetch_fn(symbol, cursor, chunk_end)
        if df_chunk is not None and not df_chunk.empty:
            chunks.append(df_chunk)

        cursor = chunk_end

    if not chunks:
        return pd.DataFrame()

    result = pd.concat(chunks, ignore_index=True)

    if "timestamp" in result.columns:
        result = result.drop_duplicates(subset="timestamp").sort_values("timestamp")
        result = result.reset_index(drop=True)

    return result
