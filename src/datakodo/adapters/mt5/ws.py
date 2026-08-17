"""MT5 streaming (Windows-only).

MT5 has no push feed: the Python package is a blocking COM wrapper, and the
closest thing to a live quote is a poll of ``MT5REST.symbol_info_tick``.
The adapter therefore advertises ``supports_streaming_* = False`` and the
fetch pipeline never opens a stream. This file exists to keep the
terminal/rest/ws layout parallel to the other adapters and to answer "why
is there no streaming?" with the explanation above.
"""
