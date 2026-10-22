"""Canonical data models — the single source of truth for output schemas.

Every adapter converts raw provider responses into these fixed shapes.
All models are Pydantic-based for validation, type safety, and serialization.

The OHLCV schema defines an invariant minimum (the columns every adapter
guarantees) plus a set of optional, opt-in columns that providers may add.
The ``columns`` parameter on ``fetch_ohlcv`` selects between the basic shape,
everything a provider offers, or an explicit list of extras.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from datakodo.core.enums import AssetClass, InstrumentType, Session

# Canonical OHLCV column sets. The base set is what every adapter must produce;
# the optional set is additive and only present when a provider offers the data
# and the caller opts in (``columns="all"`` or an explicit list).
OHLCV_BASE_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume", "is_closed")
OHLCV_OPTIONAL_COLUMNS = (
    "session",
    "close_timestamp",
    "quote_volume",
    "trades_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "vwap",
    "turnover",
    "spread",
    "real_volume",
)


def available_ohlcv_extras(columns) -> tuple[str, ...]:
    """Canonical optional columns actually present in a produced frame.

    ``columns`` is any iterable of produced column names (e.g. ``df.columns``).
    Intersecting with ``OHLCV_OPTIONAL_COLUMNS`` keeps ``columns="all"``
    honest: it surfaces exactly the extras the adapter mapped for this call
    rather than a hardcoded list (design doc sec 3).
    """
    present = set(columns)
    return tuple(col for col in OHLCV_OPTIONAL_COLUMNS if col in present)


def resolve_ohlcv_columns(columns, available) -> list[str]:
    """Resolve a ``columns`` request to a concrete list of canonical columns.

    ``columns`` is one of:

    - ``None`` or ``"basic"`` — the base OHLCV columns only (the default).
    - ``"all"`` — the base columns plus every optional column present in
      ``available``.
    - a list/tuple — extra optional columns to add on top of the base set;
      requesting a column the provider does not offer raises ``ValueError``.

    ``available`` is the collection of optional columns the provider actually
    mapped for this call. The base set is always included because it is what
    defines an OHLCV frame.
    """
    base = list(OHLCV_BASE_COLUMNS)

    if columns is None or columns == "basic":
        return base

    if columns == "all":
        return base + [col for col in OHLCV_OPTIONAL_COLUMNS if col in available]

    if isinstance(columns, (list, tuple, set)):
        extras = list(columns)
        unknown = [col for col in extras if col not in OHLCV_OPTIONAL_COLUMNS]
        if unknown:
            raise ValueError(
                f"Unknown OHLCV column(s): {unknown}. "
                f"Valid extras are {list(OHLCV_OPTIONAL_COLUMNS)}."
            )
        missing = [col for col in extras if col not in available]
        if missing:
            raise ValueError(
                f"Column(s) {missing} are not available from this provider "
                f"(available extras: {sorted(available)})."
            )
        return base + extras

    raise ValueError(
        f"Invalid columns value {columns!r}. Use 'basic', 'all', or a list of extra column names."
    )


class OHLCV(BaseModel):
    """Canonical OHLCV candle.

    ``timestamp`` is always the bar open time, in UTC. ``is_closed`` marks
    whether the bar is final or still forming. ``session`` is only present for
    session-based asset classes (equity, forex) and is ``None`` otherwise.
    """

    schema_version: str = "1.0"
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True
    session: Session | None = Field(default=None)

    # Optional extras, present only under ``columns="all"`` / an explicit list.
    close_timestamp: datetime | None = None
    quote_volume: float | None = None
    trades_count: int | None = None
    taker_buy_base_volume: float | None = None
    taker_buy_quote_volume: float | None = None
    vwap: float | None = None
    turnover: float | None = None
    spread: float | None = None
    real_volume: float | None = None


class Trade(BaseModel):
    """Canonical trade/tick record.

    ``side`` is the aggressor/taker side. ``trade_id`` is the provider's own
    identifier where available; it is what makes dedup and gap detection
    reliable.
    """

    schema_version: str = "1.0"
    timestamp: datetime
    price: float
    size: float
    side: str | None = Field(default=None)
    trade_id: int | None = Field(default=None)


class OrderBookLevel(BaseModel):
    """Single price level in an order book."""

    schema_version: str = "1.0"
    price: float
    size: float


class OrderBook(BaseModel):
    """Canonical order book snapshot.

    ``bids`` and ``asks`` are sorted best-first. ``last_update_id`` carries the
    provider's sequence number so delta reconstruction can be built later
    without a schema change.
    """

    schema_version: str = "1.0"
    timestamp: datetime
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    last_update_id: int | None = Field(default=None)


class Fundamentals(BaseModel):
    """Canonical fundamentals / reference data (design doc sec 3).

    Uses a minimal common base (fields true across asset classes) plus an
    optional asset-class-specific block, mirroring the Instrument design
    (sec 4). Providers that expose more detail subclass or extend the
    asset-class block; the base stays stable.
    """

    schema_version: str = "1.0"
    symbol: str
    name: str | None = None
    asset_class: AssetClass | None = None
    instrument_type: InstrumentType | None = None
    currency: str | None = None
    exchange: str | None = None
    as_of: datetime | None = None

    crypto: "CryptoFundamentals | None" = None


class ReferenceData(BaseModel):
    """Common base for asset-class-specific reference data."""

    schema_version: str = "1.0"


class CryptoFundamentals(ReferenceData):
    """Crypto-specific fundamentals from Binance exchange info / ticker.

    ``status`` reflects trading status; ``permissions`` lists the trading
    modes (e.g. ``["SPOT"]`` or ``["TRADING"]`` for futures).
    """

    base_asset: str = ""
    quote_asset: str = ""
    status: str = ""
    is_spot_trading_allowed: bool | None = None
    is_margin_trading_allowed: bool | None = None
    permissions: list[str] = []
    asset_class: AssetClass = AssetClass.CRYPTO
    instrument_type: InstrumentType = InstrumentType.SPOT

    # Live price/volume stats from the 24h rolling ticker. These are
    # crypto-specific, so they live here rather than on the shared base.
    latest_price: float | None = None
    price_change_24h: float | None = None
    open_24h: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    volume_24h: float | None = None
    quote_volume_24h: float | None = None
