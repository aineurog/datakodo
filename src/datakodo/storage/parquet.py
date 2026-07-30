"""Parquet storage backend — default for Phase 1.

Zero setup, fast columnar reads, no server required.
Data is partitioned by symbol and date for query performance.
"""


class ParquetBackend:
    """Local Parquet storage implementing the StorageBackend interface."""

    def __init__(self, base_dir: str = "datakodo_cache") -> None:
        self._base_dir = base_dir

    def write(self, key: str, data: object) -> None:
        """Write data to a Parquet file keyed by *key*."""
        raise NotImplementedError("Parquet backend not yet implemented")

    def read(self, key: str) -> object:
        """Read data from a Parquet file keyed by *key*."""
        raise NotImplementedError("Parquet backend not yet implemented")

    def exists(self, key: str) -> bool:
        """Check whether a Parquet file exists for *key*."""
        return False
