"""Standardized exception hierarchy.

Every adapter catches provider-native exceptions and maps them into
this hierarchy so users never need to handle provider-specific errors.
"""


class DataLibError(Exception):
    """Base exception for all DataKodo errors."""


class AuthenticationError(DataLibError):
    """API key missing, invalid, or expired."""


class RateLimitError(DataLibError):
    """Provider rate limit hit."""

    def __init__(self, message: str = "", retry_after: float = 0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SymbolNotFoundError(DataLibError):
    """Instrument symbol not recognized by the provider."""


class InvalidTimeframeError(DataLibError):
    """Requested timeframe is not supported by the provider."""


class ConnectionError(DataLibError):
    """Network or transport-level failure."""


class TimeoutError(DataLibError):
    """A request timed out. Distinct from ``ConnectionError``: a timeout is a
    single stalled request and carries different retry semantics than a
    transport failure."""


class RetriesExhaustedError(DataLibError):
    """Exponential backoff gave up after the configured retry budget."""


class DataNotAvailableError(DataLibError):
    """Data exists but is gated behind a tier / subscription level."""


class NotSupportedError(DataLibError):
    """The adapter does not support the requested capability at all."""


class PaidTierRequiredError(DataLibError):
    """The requested endpoint requires a paid provider tier."""


class DataValidationError(DataLibError):
    """The provider returned data that failed quality checks."""


class ProviderError(DataLibError):
    """Raw passthrough — wraps the original provider exception as last resort.

    The original exception is available via `__cause__`.
    """

    def __init__(self, message: str = "", original: Exception | None = None) -> None:
        super().__init__(message)
        if original is not None:
            self.__cause__ = original
