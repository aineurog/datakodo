"""Parquet storage backend — default for Phase 1.

Zero setup, fast columnar reads, no server required. Data is partitioned
by a user-supplied key (typically a cache key such as
``binance-spot/BTCUSDT/1h/start_end``) and written as one .parquet file.
"""

from pathlib import Path

import pandas as pd

from datakodo.core.interfaces import StorageBackend


class ParquetBackend(StorageBackend):
    """Local Parquet storage implementing the StorageBackend interface."""

    def __init__(self, base_dir: str = "datakodo_cache") -> None:
        self._base_dir = Path(base_dir)

    def _path(self, key: str) -> Path:
        """Map a logical key to a .parquet file underneath the base dir.

        Cache keys may contain timestamp separators (``:``) that are invalid
        on Windows paths, so they are normalized before use.
        """
        safe = key.replace(":", "-")
        return self._base_dir / f"{safe}.parquet"

    def write(self, key: str, data: object) -> None:
        """Write a DataFrame to a Parquet file keyed by *key*."""
        if not isinstance(data, pd.DataFrame):
            raise TypeError(f"ParquetBackend only stores DataFrames, got {type(data).__name__}")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        data.to_parquet(path)

    def read(self, key: str) -> object:
        """Read a DataFrame from a Parquet file keyed by *key*."""
        path = self._path(key)
        if not path.exists():
            raise KeyError(f"No cached data for key {key!r}")
        return pd.read_parquet(path)

    def exists(self, key: str) -> bool:
        """Check whether a Parquet file exists for *key*."""
        return self._path(key).exists()
