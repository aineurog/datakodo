"""MT5 COM-based terminal connection (blocking).

The MetaTrader 5 Python package is natively blocking. This module wraps
initialization, shutdown, and OHLCV retrieval behind a small interface.

Timezone handling: MT5 reports every timestamp in **server time** and
interprets request datetimes as server time too. DataKodo works in UTC
(design doc sec 2), so requests are shifted into server time and returned
raw timestamps are converted back to true UTC.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from datakodo.core.config import Config
from datakodo.core.exceptions import ConnectionError, RateLimitError
from datakodo.ratelimit.limiter import TokenBucket

logger = logging.getLogger(__name__)


def _resolve_terminal_path(path: str) -> str:
    """Return the MT5 executable for ``initialize()``.

    *path* may be an install folder (e.g. ``C:\\Program Files\\MetaTrader 5``)
    or the ``terminal64.exe`` path itself. Folders are resolved to
    ``terminal64.exe`` (with ``terminal.exe`` as a fallback).
    """
    if not path:
        return ""
    candidate = Path(path)
    if candidate.is_dir():
        for name in ("terminal64.exe", "terminal.exe"):
            exe = candidate / name
            if exe.is_file():
                return str(exe)
        return str(candidate)
    return path


def _load_mt5() -> Any:
    """Import and return the ``MetaTrader5`` module (Windows-only).

    Imported lazily so the rest of DataKodo and the test suite run on
    platforms where the module is unavailable.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:  # pragma: no cover - platform-specific
        raise ImportError(
            "The MetaTrader5 package is required for the MT5 adapter "
            "(pip install 'datakodo[mt5]'). It is Windows-only."
        ) from exc
    return mt5


def _as_utc(dt: datetime) -> datetime:
    """Return *dt* as a UTC-aware datetime (naive input is assumed UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class MT5Terminal:
    """Wraps the MetaTrader 5 terminal connection (Windows-only, blocking)."""

    def __init__(self, terminal_path: str = "", config: Config | None = None) -> None:
        self._path = terminal_path
        self._config = config or Config()
        self._mt5: Any = None
        self._connected = False
        # Conservative token bucket around data requests (design doc sec 16).
        # MT5 throttles symbol/data requests; see Config.mt5_rate_limit_*.
        self._limiter = TokenBucket(
            rate=self._config.mt5_rate_limit_rate,
            burst=self._config.mt5_rate_limit_burst,
        )

    def server_offset_seconds(self, symbol: str) -> int:
        """Difference between the server clock and UTC, in seconds.

        Broker servers use whole-hour timezone offsets (e.g. GMT+3). The
        ``tick.time`` value can lag the real clock by a second, so the
        offset is snapped to the nearest hour to keep bars on exact
        ``:00`` UTC boundaries.
        """
        tick = self._mt5.symbol_info_tick(symbol)
        if tick is None:
            return 0
        raw = tick.time - time.time()
        return int(round(raw / 3600.0)) * 3600

    def initialize(self) -> bool:
        """Connect to the MT5 terminal and confirm an account is logged in.

        Uses the configured login/password/server when provided; otherwise
        attaches to the terminal's default (last-used) account. *path*
        points at ``terminal64.exe``; empty uses the default install.

        Raises ``ConnectionError`` when the terminal cannot be reached
        (e.g. IPC failure or bad credentials).
        """
        if self._connected:
            return True

        mt5 = _load_mt5()
        cfg = self._config
        path = _resolve_terminal_path(self._path or cfg.mt5_terminal_path)
        kwargs: dict[str, Any] = {}
        if cfg.mt5_login:
            kwargs["login"] = cfg.mt5_login
        if cfg.mt5_password:
            kwargs["password"] = cfg.mt5_password
        if cfg.mt5_server:
            kwargs["server"] = cfg.mt5_server

        ok = mt5.initialize(path, **kwargs) if path else mt5.initialize(**kwargs)
        if not ok:
            code, desc = mt5.last_error() or (-1, "unknown error")
            raise ConnectionError(
                f"MT5 initialize() failed: code={code} ({desc}). "
                f"Check login/password/server in .env or log in manually in the terminal."
            )

        self._mt5 = mt5
        self._connected = True

        # Bad credentials can still open the terminal while staying logged
        # out; surface that so the user isn't surprised by empty fetches.
        account = mt5.account_info()
        if account is None:
            logger.warning(
                "MT5 terminal connected but no account is logged in. "
                "Check login/password/server in .env or log in manually in the terminal."
            )
        else:
            logger.info("MT5 logged in as %s @ %s.", account.login, account.server)
        return True

    def shutdown(self) -> None:
        """Close the MT5 terminal connection."""
        if self._mt5 is not None:
            self._mt5.shutdown()
        self._mt5 = None
        self._connected = False
        logger.info("MT5 terminal connection closed.")

    @property
    def connected(self) -> bool:
        """True when the terminal connection is established."""
        return self._connected

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
                "No %s history returned for %s [%s → %s]. "
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
        classify a symbol as spot, futures, CFD, etc. (``map_instrument``).
        """
        if not self._connected:
            raise ConnectionError("MT5 terminal is not connected.")
        self._acquire(1)
        return self._mt5.symbol_info(symbol)

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        """Add/remove ``symbol`` from the MarketWatch window, returning success.

        MT5 only subscribes quotes/rates for symbols visible in MarketWatch
        (design doc sec "reference"). Calling ``symbol_select(symbol, True)``
        before reading data ensures history is available for the symbol.
        """
        if not self._connected:
            raise ConnectionError("MT5 terminal is not connected.")
        self._acquire(1)
        return bool(self._mt5.symbol_select(symbol, enable))

    def symbol_info_tick(self, symbol: str) -> Any:
        """Return the latest raw ``Tick`` tuple for *symbol*, or ``None``.

        The tick carries bid/ask/last prices, last volume, and the quote time
        — the live-price inputs used by ``map_fundamentals``.
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
