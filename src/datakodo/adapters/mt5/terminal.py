"""MT5 terminal connection (blocking COM, Windows-only).

Owns the MetaTrader 5 terminal session: ``initialize`` / ``shutdown`` and the
live module handle. Blocking data access lives in ``rest.py``; MT5 has no
streaming (see ``ws.py``), so this file is deliberately small.
"""

import logging
from pathlib import Path
from typing import Any

from datakodo.adapters.mt5.config import MT5Config
from datakodo.core.exceptions import ConnectionError

logger = logging.getLogger(__name__)

_MT5_INITIALIZE_TIMEOUT_MS = 60_000


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


class MT5Terminal:
    """Owns the MetaTrader 5 terminal session (Windows-only, blocking).

    Connection lifecycle only — data requests live on ``MT5REST``
    (``datakodo.adapters.mt5.rest``).
    """

    def __init__(self, terminal_path: str = "", mt5_config: MT5Config | None = None) -> None:
        self._config = mt5_config or MT5Config()
        self._path = terminal_path or self._config.terminal_path
        self._mt5: Any = None
        self._connected = False

    @property
    def config(self) -> MT5Config:
        """The settings this terminal was created with."""
        return self._config

    @property
    def module(self) -> Any:
        """The live ``MetaTrader5`` module once initialized, else ``None``."""
        return self._mt5

    @property
    def connected(self) -> bool:
        """True when the terminal connection is established."""
        return self._connected

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
        path = _resolve_terminal_path(self._path)
        kwargs: dict[str, Any] = {"timeout": _MT5_INITIALIZE_TIMEOUT_MS}
        if cfg.login:
            kwargs["login"] = cfg.login
        if cfg.password:
            kwargs["password"] = cfg.password
        if cfg.server:
            kwargs["server"] = cfg.server

        ok = mt5.initialize(path, **kwargs) if path else mt5.initialize(**kwargs)
        if not ok:
            code, desc = mt5.last_error() or (-1, "unknown error")
            raise ConnectionError(
                f"MT5 initialize() failed: code={code} ({desc}). "
                f"Check login/password/server in .env (MT5_*) or log in manually in the terminal."
            )

        self._mt5 = mt5
        self._connected = True

        # Bad credentials can still open the terminal while staying logged
        # out; surface that so the user isn't surprised by empty fetches.
        account = mt5.account_info()
        if account is None:
            logger.warning(
                "MT5 terminal connected but no account is logged in. "
                "Check login/password/server in .env (MT5_*) or log in manually in the terminal."
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
