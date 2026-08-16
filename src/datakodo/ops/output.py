"""User-facing output format conversion (design doc sec 13).

Adapters produce a columnar, vectorized frame; this module converts it cheaply
to whichever output format the user requested — pandas (default), polars, or
arrow. It is the single conversion point used by adapters so users never
handle raw provider data directly.
"""

from typing import Any

import pandas as pd

SUPPORTED_OUTPUT_FORMATS = ("pandas", "polars", "arrow")


def to_output_format(df: pd.DataFrame, output_format: str = "pandas") -> Any:
    """Convert a canonical OHLCV frame to *output_format*.

    Args:
        df: Canonical OHLCV pandas DataFrame.
        output_format: One of ``pandas`` (default), ``polars``, or ``arrow``.

    Returns:
        The same frame for ``pandas``, a ``polars.DataFrame``, or a
        ``pyarrow.Table`` respectively.

    Raises:
        ValueError: If *output_format* is not supported.
    """
    if output_format == "pandas":
        return df
    if output_format == "polars":
        import polars as pl

        return pl.from_pandas(df)
    if output_format == "arrow":
        import pyarrow as pa

        return pa.Table.from_pandas(df)

    raise ValueError(
        f"Unsupported output format {output_format!r}. Choose from {SUPPORTED_OUTPUT_FORMATS}."
    )
