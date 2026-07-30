"""Schema validation tests — the most rigorous coverage in the project.

Design doc sec 26: canonical schema is the core value proposition.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from datakodo.core.schemas import OHLCV, OrderBook, OrderBookLevel, Trade


class TestOHLCV:
    def test_valid_ohlcv(self):
        ohlcv = OHLCV(
            timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            open=100.0,
            high=105.0,
            low=99.0,
            close=104.0,
            volume=5000.0,
        )
        assert ohlcv.open == 100.0
        assert ohlcv.high == 105.0
        assert ohlcv.schema_version == "1.0"

    def test_ohlcv_session_is_optional(self):
        ohlcv = OHLCV(
            timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            open=100.0,
            high=105.0,
            low=99.0,
            close=104.0,
            volume=5000.0,
        )
        assert ohlcv.session is None

    def test_ohlcv_session_value(self):
        ohlcv = OHLCV(
            timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            open=100.0,
            high=105.0,
            low=99.0,
            close=104.0,
            volume=5000.0,
            session="regular",
        )
        assert ohlcv.session == "regular"

    def test_ohlcv_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            OHLCV(
                timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
                open=100.0,
                # missing high, low, close, volume
            )


class TestTrade:
    def test_valid_trade(self):
        trade = Trade(
            timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            price=100.5,
            size=0.1,
        )
        assert trade.price == 100.5
        assert trade.side is None
        assert trade.schema_version == "1.0"

    def test_trade_with_side(self):
        trade = Trade(
            timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            price=100.5,
            size=0.1,
            side="buy",
        )
        assert trade.side == "buy"


class TestOrderBook:
    def test_valid_order_book(self):
        book = OrderBook(
            timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            bids=[OrderBookLevel(price=99.0, size=1.0)],
            asks=[OrderBookLevel(price=101.0, size=2.0)],
        )
        assert len(book.bids) == 1
        assert book.bids[0].price == 99.0
        assert book.asks[0].size == 2.0
        assert book.schema_version == "1.0"

    def test_empty_order_book(self):
        book = OrderBook(
            timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            bids=[],
            asks=[],
        )
        assert book.bids == []
        assert book.asks == []
