"""Cache key generation, invalidation rules, and CacheEntry model.

Design doc sec 17: caching avoids re-fetching data already on disk.
Only closed/final data is cached; the current incomplete candle is
always re-fetched live.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass
class CacheEntry:
    """Metadata for a single cached data range."""

    provider: str
    symbol: str
    timeframe: str
    date_range: tuple[str, str]
    fetched_at: datetime
    is_closed: bool
    expiry: datetime | None = None


def build_cache_key(provider: str, symbol: str, timeframe: str, date_range: tuple[str, str]) -> str:
    """Build a deterministic cache key from provider and query parameters.

    The key is provider-specific since data quality and adjustments can
    differ between providers for the same symbol.
    """
    start, end = date_range
    return f"{provider}/{symbol}/{timeframe}/{start}_{end}"


def is_bar_closed(timestamp: datetime, timeframe: str) -> bool:
    """Return True if the candle/bar ending at *timestamp* is complete.

    A bar is closed when its end time is in the past relative to now.
    The current (still-forming) bar is always open.
    """
    now = datetime.now(UTC)
    return timestamp < now


def compute_expiry(timeframe: str) -> datetime:
    """Return a suggested expiry time for cached data at this timeframe.

    Closed historical data is immutable and has no expiry (returns
    a far-future sentinel). Shorter timeframes get shorter refresh
    windows since the "latest closed bar" changes more often.
    """
    minute_frames = {"1m", "5m", "15m", "30m"}
    hour_frames = {"1h", "4h"}

    now = datetime.now(UTC)
    if timeframe in minute_frames:
        return now + timedelta(minutes=5)
    elif timeframe in hour_frames:
        return now + timedelta(hours=1)
    else:
        # Daily and above — closed data is immutable.
        return now + timedelta(days=365 * 10)
