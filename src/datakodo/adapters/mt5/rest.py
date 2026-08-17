"""MT5 blocking data access (Windows-only).

MT5's Python API is COM-based and blocking — there is no HTTP REST layer.
``MT5REST`` mirrors the shape of a data REST client while talking directly
to the terminal session owned by :class:`MT5Terminal`. Live streamed feeds
do not exist either (see ``ws.py``); the closest thing is a blocking tick
poll via ``symbol_info_tick``.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from datakodo.adapters.mt5.terminal import MT5Terminal
from datakodo.core.exceptions import ConnectionError, RateLimitError
from datakodo.ratelimit.limiter import TokenBucket

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime) -> datetime:
    """Return *dt* as a UTC-aware datetime (naive input is assumed UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class MT5REST:
    """Blocking market-data access to a connected MT5 terminal.

    Owns nothing the terminal owns: the session, the module handle, and the
    connection flag all live on the :class:`MT5Terminal` passed in, so a
    ``rest``/``terminal`` pair cannot drift apart. Requests are gated by a
    token bucket (design doc sec 17; see ``MT5Config.rate_limit_*``).
    """

    def __init__(self, terminal: MT5Terminal) -> None:
        self._terminal = terminal
        self._limiter = TokenBucket(
            rate=terminal.config.rate_limit_rate,
            burst=terminal.config.rate_limit_burst,
        )

    @property
    def _mt5(self) -> Any:
        """The live ``MetaTrader5`` module, or ``None`` when disconnected."""
        return self._terminal.module

    @property
    def _connected(self) -> bool:
        return self._terminal.connected

    def server_offset_seconds(self, symbol: str) -> int:
        """Difference between the server clock and UTC, in seconds.

        Broker servers use whole-hour timezone offsets (e.g. GMT+3). The
        ``tick.time`` value can lag the real clock by a second, so the
        offset is snapped to the nearest hour to keep bars on exact
        ``:00`` UTC boundaries.
        """
        if not self._connected:
            raise ConnectionError("MT5 terminal is not connected.")
        tick = self._mt5.symbol_info_tick(symbol)
        if tick is None:
            return 0
        raw = tick.time - time.time()
        return int(round(raw / 3600.0)) * 3600

    def _acquire(self, weight: int = 1) -> None:
        """Consume request tokens, raising ``RateLimitError`` when empty."""
        if not self._limiter.consume(weight):
            retry_after = self._limiter.wait_time(weight)
            raise RateLimitError(
                f"MT5 rate limit exceeded. Retry after {retry_after:.1f}s.",
                retry_after=retry_after,
            )

    def copy_rates_range(self, symbol: str, timeframe: int, start: datetime, end: datetime) -> Any:
        """Fetch raw OHLCV rates for *symbol* over the given date range.

        ``timeframe`` is an MT5 ``TIMEFRAME_*`` integer constant (see
        ``core.timeframe.MT5_MAP``). ``start``/``end`` are UTC datetimes;
        the request is shifted into **server time** (via
        ``server_offset_seconds``) so it reaches the currently-forming bar.

        Returns the MT5 numpy structured array (native format, timestamps
        still in server time) or ``None`` when the terminal has no history
        in the requested window.
        """
        if not self._connected:
            raise ConnectionError("MT5 terminal is not connected.")
        self._acquire(1)
        start_utc = _as_utc(start)
        end_utc = _as_utc(end)
        shift = timedelta(seconds=self.server_offset_seconds(symbol))
        rates = self._mt5.copy_rates_range(symbol, timeframe, start_utc + shift, end_utc + shift)
        if rates is None or len(rates) == 0:
            # No history in the terminal for this window. This usually means
            # the symbol's chart was never opened, or 'Max. bars in chart' is
            # set too low for the requested window.
            logger.warning(
                "No %s history returned for %s [%s \u2192 %s]. "
                "Open the %s chart in MT5 (or raise 'Max. bars in chart') so "
                "history is loaded, then retry.",
                symbol,
                timeframe,
                start_utc.isoformat(),
                end_utc.isoformat(),
                symbol,
            )
            return None
        return rates

    # -- symbol metadata (spot vs futures classification) --

    def symbol_info(self, symbol: str) -> Any:
        """Return the raw ``SymbolInfo`` tuple for *symbol*, or ``None``.

        The tuple carries ``trade_calc_mode``, ``path`` (Market Watch tree),
        contract/tick sizes, currencies, and expiry — the fields used to
        classify a symbol as spot, futures, CFD, etc.
        """
        if not self._connected:
            raise ConnectionError("MT5 terminal is not connected.")
        self._acquire(1)
        return self._mt5.symbol_info(symbol)

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        """Add/remove ``symbol`` from the MarketWatch window, returning success.

        MT5 only subscribes quotes/rates for symbols visible in MarketWatch.
        Calling ``symbol_select(symbol, True)`` before reading data ensures
        history is available for the symbol.
        """
        if not self._connected:
            raise ConnectionError("MT5 terminal is not connected.")
        self._acquire(1)
        return bool(self._mt5.symbol_select(symbol, enable))

    def symbol_info_tick(self, symbol: str) -> Any:
        """Return the latest raw ``Tick`` tuple for *symbol*, or ``None``.

        The tick carries bid/ask/last prices, last volume, and the quote time
        — the live-price inputs used for fundamentals. This is the closest
        MT5 gets to streaming: a blocking poll, not a push feed.
        """
        if not self._connected:
            raise ConnectionError("MT5 terminal is not connected.")
        self._acquire(1)
        return self._mt5.symbol_info_tick(symbol)

    def futures_calc_modes(self) -> frozenset[int]:
        """``trade_calc_mode`` integers that identify futures contracts.

        MT5 exposes these as module-level ``SYMBOL_CALC_MODE_*`` constants
        whose numeric values vary by package build (unlike the MQL5 docs),
        so they are read from the live module rather than hardcoded.
        """
        return self._calc_modes(
            "SYMBOL_CALC_MODE_FUTURES",
            "SYMBOL_CALC_MODE_EXCH_FUTURES",
        )

    def forex_calc_modes(self) -> frozenset[int]:
        """``trade_calc_mode`` integers that identify spot forex pairs.

        Value resolution mirrors ``futures_calc_modes`` — the forex calc-mode
        integers (``SYMBOL_CALC_MODE_FOREX`` and ``..._FOREX_NO_LEVERAGE``)
        also vary by package build, so they are read from the live module.
        """
        return self._calc_modes(
            "SYMBOL_CALC_MODE_FOREX",
            "SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE",
        )

    def _calc_modes(self, *names: str) -> frozenset[int]:
        """Resolve the numeric values of the given ``SYMBOL_CALC_MODE_*`` names."""
        if self._mt5 is None:
            return frozenset()
        return frozenset(getattr(self._mt5, name) for name in names if hasattr(self._mt5, name))
