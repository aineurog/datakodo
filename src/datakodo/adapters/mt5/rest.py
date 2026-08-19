"""MT5 blocking data access (Windows-only).

MT5's Python API is COM-based and blocking - there is no HTTP REST layer.
``MT5REST`` mirrors the shape of a data REST client while talking directly
to the terminal session owned by :class:`MT5Terminal`. Live streamed feeds
do not exist either (see ``ws.py``); the closest thing is a blocking tick
poll via ``symbol_info_tick``.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from datakodo.adapters.mt5.terminal import MT5Terminal
from datakodo.core.config import Config
from datakodo.core.exceptions import (
    ConnectionError,
    ProviderError,
    RateLimitError,
    RetriesExhaustedError,
    SymbolNotFoundError,
)
from datakodo.ratelimit.limiter import TokenBucket

logger = logging.getLogger(__name__)

# MT5 ``last_error()`` codes that mean the symbol itself is unknown to the
# terminal (ERR_UNKNOWN_SYMBOL / ERR_SYMBOL_NOT_FOUND). Any other empty result
# (chart never opened, 'Max. bars in chart' too low, ...) stays a no-history
# result, which the adapter surfaces as DataNotAvailableError.
_UNKNOWN_SYMBOL_CODES = frozenset({4108, 4401})
_UNKNOWN_SYMBOL_HINTS = ("unknown symbol", "symbol not found", "symbol does not exist")


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
    token bucket (design doc sec 17; see ``MT5Config.rate_limit_*``) and
    rate-limit hits retry with exponential backoff governed by
    ``Config.max_retries`` / ``Config.retry_base_delay`` (design doc sec 16).
    """

    def __init__(
        self,
        terminal: MT5Terminal,
        config: Config | None = None,
    ) -> None:
        self._terminal = terminal
        self._config = config or Config()
        self._limiter = TokenBucket(
            rate=terminal.config.rate_limit_rate,
            burst=terminal.config.rate_limit_burst,
        )
        self._watchlist_added: set[str] = set()

    @property
    def _mt5(self) -> Any:
        """The live ``MetaTrader5`` module, or ``None`` when disconnected."""
        return self._terminal.module

    @property
    def _connected(self) -> bool:
        return self._terminal.connected

    def _ready(self, weight: int = 0) -> Any:
        """Return the live module after the connection and rate-limit gates.

        Raises ``ConnectionError`` when the terminal is not connected, and
        ``RateLimitError`` when the token bucket is empty for ``weight > 0``
        requests. Every data call funnels through here so the guards stay
        consistent (design doc sec 17).
        """
        if not self._connected:
            raise ConnectionError("MT5 terminal is not connected.")
        if weight and not self._limiter.consume(weight):
            retry_after = self._limiter.wait_time(weight)
            raise RateLimitError(
                f"MT5 rate limit exceeded. Retry after {retry_after:.1f}s.",
                retry_after=retry_after,
            )
        return self._mt5

    def _with_retry(self, weight: int, fn: Callable[[Any], Any]) -> Any:
        """Run ``fn(module)`` under the rate gate with retry/backoff.

        MT5's terminal is local, so an empty token bucket is a transient
        condition: the request backs off and retries up to ``Config.max_retries``
        times with exponential delay (``Config.retry_base_delay``). When the
        retry budget is exhausted, raises ``RetriesExhaustedError`` (design doc
        sec 16/17).
        """
        last_exc: Exception | None = None
        retried = False
        max_retries = self._config.max_retries
        base_delay = self._config.retry_base_delay
        for attempt in range(max_retries + 1):
            try:
                return fn(self._ready(weight))
            except RateLimitError as exc:
                if attempt >= max_retries:
                    if retried:
                        break
                    raise exc
                delay = base_delay * (2**attempt)
                retry_after = max(delay, exc.retry_after)
                logger.info(
                    "MT5 rate-limited (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    retry_after,
                )
                time.sleep(retry_after)
                last_exc = exc
                retried = True
        raise RetriesExhaustedError(
            f"MT5 request failed after {max_retries + 1} attempts."
        ) from last_exc

    def server_offset_seconds(self, symbol: str) -> int:
        """Difference between the server clock and UTC, in seconds.

        Broker servers use whole-hour timezone offsets (e.g. GMT+3). The
        ``tick.time`` value can lag the real clock by a second, so the
        offset is snapped to the nearest hour to keep bars on exact
        ``:00`` UTC boundaries.
        """
        tick = self._ready().symbol_info_tick(symbol)
        if tick is None:
            return 0
        raw = tick.time - time.time()
        return int(round(raw / 3600.0)) * 3600

    def copy_rates_range(
        self,
        symbol: str,
        timeframe: int,
        start: datetime,
        end: datetime,
        *,
        offset_seconds: int | None = None,
    ) -> Any:
        """Fetch raw OHLCV rates for *symbol* over the given date range.

        ``timeframe`` is an MT5 ``TIMEFRAME_*`` integer constant (see
        ``core.timeframe.MT5_MAP``). ``start``/``end`` are UTC datetimes;
        the request is shifted into **server time** (via
        ``server_offset_seconds``) so it reaches the currently-forming bar.
        Pass a pre-computed ``offset_seconds`` to skip the extra tick poll.

        Returns the MT5 numpy structured array (native format, timestamps
        still in server time) or ``None`` when the terminal has no history
        in the requested window. A symbol unknown to the terminal raises
        ``SymbolNotFoundError`` (via ``last_error()``).
        """
        start_utc = _as_utc(start)
        end_utc = _as_utc(end)

        def _copy(mt5: Any) -> Any:
            offset = offset_seconds
            if offset is None:
                offset = self.server_offset_seconds(symbol)
            shift = timedelta(seconds=offset)
            return mt5.copy_rates_range(symbol, timeframe, start_utc + shift, end_utc + shift)

        rates = self._with_retry(1, _copy)
        if rates is None or len(rates) == 0:
            code, description = self._ready().last_error()
            if self._is_unknown_symbol(code, description):
                raise SymbolNotFoundError(
                    f"Symbol {symbol!r} not found on MT5: {description or f'code {code}'}"
                )
            # No history in the terminal for this window. This usually means
            # the symbol's chart was never opened (download is still running
            # in the background), or 'Max. bars in chart' is set too low for
            # the requested window.
            logger.warning(
                "No %s history returned for %s [%s \u2192 %s]. "
                "Data is downloading in the background - this may take some "
                "time. Open the %s chart in MT5 (or raise 'Max. bars in "
                "chart') to speed it up.",
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
        contract/tick sizes, currencies, and expiry - the fields used to
        classify a symbol as spot, futures, CFD, etc.
        """
        return self._with_retry(1, lambda mt5: mt5.symbol_info(symbol))

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        """Add/remove ``symbol`` from the MarketWatch window, returning success.

        MT5 only subscribes quotes/rates for symbols visible in MarketWatch.
        Calling ``symbol_select(symbol, True)`` before reading data ensures
        history is available for the symbol.
        """
        return bool(self._with_retry(1, lambda mt5: mt5.symbol_select(symbol, enable)))

    def ensure_symbol_known(self, symbol: str) -> None:
        """Ensure *symbol* is selected in MarketWatch, raising on failure.

        ``symbol_select(True)`` also kicks off the terminal's history download,
        so MT5 loads bars for a symbol whose chart was never opened. A
        ``False`` result means either the terminal does not recognize the
        symbol (→ ``SymbolNotFoundError``) or the select failed for another
        reason - e.g. ``Terminal: Out of memory`` (→ ``ProviderError`` with the
        terminal's own description).

        The two cases are told apart by ``symbol_info``: a symbol the terminal
        does not know at all returns ``None``, while a real terminal failure
        (out of memory, IPC hiccup, ...) still resolves the symbol's info.
        """
        if not self.symbol_select(symbol, True):
            code, description = self._ready().last_error()
            detail = description or f"code {code}"
            if self.symbol_info(symbol) is None or self._is_unknown_symbol(code, description):
                raise SymbolNotFoundError(
                    f"Symbol {symbol!r} is not recognized by the MT5 terminal "
                    f"(symbol_select failed: {detail}). "
                    f"It was not added to the watchlist - it does not exist on this server."
                )
            raise ProviderError(f"MT5 symbol_select({symbol!r}) failed: {detail}.")
        if symbol not in self._watchlist_added:
            self._watchlist_added.add(symbol)
            logger.info(
                "Added %s to the MarketWatch list. Data is downloading in the "
                "background - this may take some time for a symbol whose chart "
                "was never opened.",
                symbol,
            )

    def symbols_get(self) -> Any:
        """Return the terminal's full symbol list (``mt5.symbols_get``).

        The terminal serves its entire universe locally - the cheap "symbol
        list" fetch that makes ``search_instruments`` possible (design doc sec
        5). Returns the raw list of SymbolInfo tuples, or ``None`` on a
        terminal error.
        """
        return self._with_retry(1, lambda mt5: mt5.symbols_get())

    def last_error(self) -> tuple[int, str]:
        """The terminal's most recent ``(code, description)`` error pair."""
        return cast(tuple[int, str], self._ready().last_error())

    @staticmethod
    def _is_unknown_symbol(code: int, description: str) -> bool:
        """True when an MT5 ``last_error()`` pair means the symbol is unknown."""
        text = (description or "").lower()
        return code in _UNKNOWN_SYMBOL_CODES or any(hint in text for hint in _UNKNOWN_SYMBOL_HINTS)

    def symbol_info_tick(self, symbol: str) -> Any:
        """Return the latest raw ``Tick`` tuple for *symbol*, or ``None``.

        The tick carries bid/ask/last prices, last volume, and the quote time
        - the live-price inputs used for fundamentals. This is the closest
        MT5 gets to streaming: a blocking poll, not a push feed.
        """
        return self._with_retry(1, lambda mt5: mt5.symbol_info_tick(symbol))

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

        Value resolution mirrors ``futures_calc_modes`` - the forex calc-mode
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
