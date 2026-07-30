"""Exception hierarchy tests."""

from datakodo.core.exceptions import (
    AuthenticationError,
    ConnectionError,
    DataLibError,
    DataNotAvailableError,
    InvalidTimeframeError,
    NotSupportedError,
    PaidTierRequiredError,
    ProviderError,
    RateLimitError,
    SymbolNotFoundError,
)


class TestExceptionHierarchy:
    def test_all_exceptions_are_datakodo_errors(self):
        for exc_cls in [
            AuthenticationError,
            RateLimitError,
            SymbolNotFoundError,
            InvalidTimeframeError,
            ConnectionError,
            DataNotAvailableError,
            NotSupportedError,
            PaidTierRequiredError,
            ProviderError,
        ]:
            assert issubclass(exc_cls, DataLibError)

    def test_rate_limit_error_carries_retry_after(self):
        err = RateLimitError("too fast", retry_after=5.0)
        assert err.retry_after == 5.0
        assert str(err) == "too fast"

    def test_rate_limit_error_default_retry_after(self):
        err = RateLimitError()
        assert err.retry_after == 0

    def test_provider_error_wraps_original(self):
        original = ValueError("provider failure")
        err = ProviderError("wrapped", original=original)
        assert err.__cause__ is original
        assert str(err) == "wrapped"

    def test_provider_error_no_original(self):
        err = ProviderError("bare error")
        assert err.__cause__ is None


class TestExceptionMessages:
    def test_authentication_error_message(self):
        err = AuthenticationError("invalid API key")
        assert str(err) == "invalid API key"

    def test_symbol_not_found_message(self):
        err = SymbolNotFoundError("XYZ does not exist")
        assert str(err) == "XYZ does not exist"

    def test_invalid_timeframe_message(self):
        err = InvalidTimeframeError("2m not supported")
        assert str(err) == "2m not supported"

    def test_not_supported_message(self):
        err = NotSupportedError("adapter missing capability")
        assert str(err) == "adapter missing capability"

    def test_paid_tier_required_message(self):
        err = PaidTierRequiredError("upgrade needed")
        assert str(err) == "upgrade needed"

    def test_connection_error_message(self):
        err = ConnectionError("timeout")
        assert str(err) == "timeout"

    def test_data_not_available_message(self):
        err = DataNotAvailableError("historical data on paid tier only")
        assert str(err) == "historical data on paid tier only"
