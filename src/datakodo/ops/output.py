"""User-facing output format conversion (design doc sec 12).

The canonical internal representation is a pandas DataFrame; this module
converts it cheaply to whichever output format the user requested — pandas,
polars, arrow, or numpy. It is the single conversion point used by adapters
so users never handle raw provider data directly.

Design doc sec 12: pandas is the default user-facing format; polars, arrow,
and numpy are offered with no meaningful overhead since conversion from the
canonical frame is cheap.
"""

from typing import Any

import pandas as pd

SUPPORTED_OUTPUT_FORMATS = ("pandas", "polars", "arrow", "numpy")


def to_output_format(df: pd.DataFrame, output_format: str = "pandas") -> Any:
    """Convert a canonical pandas OHLCV frame to *output_format*.

    Args:
        df: Canonical OHLCV pandas DataFrame.
        output_format: One of ``pandas`` (default), ``polars``, ``arrow``,
            or ``numpy``.

    Returns:
        The same frame for ``pandas``, a ``polars.DataFrame``, a
        ``pyarrow.Table``, or a ``numpy.ndarray`` respectively.

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
    if output_format == "numpy":
        return df.to_numpy()

    raise ValueError(
        f"Unsupported output format {output_format!r}. Choose from {SUPPORTED_OUTPUT_FORMATS}."
    )
