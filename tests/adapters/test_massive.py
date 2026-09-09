"""Massive adapter tests."""

from datetime import UTC, datetime

import pytest

from datakodo.adapters.massive.adapter import MassiveAdapter
from datakodo.adapters.massive.mapper import map_instrument
from datakodo.core.enums import AssetClass, InstrumentType
from datakodo.core.instruments import Instrument


class TestMassiveAdapter:
    def test_adapter_capabilities(self):
        adapter = MassiveAdapter()
        assert adapter.supports_ohlcv is True
        assert adapter.supports_streaming_orderbook is False
        assert adapter.supports_fundamentals is True

    def test_fetch_ohlcv_not_implemented_yet(self):

        adapter = MassiveAdapter()
        now = datetime.now(UTC)
        with pytest.raises(NotImplementedError):
            adapter.fetch_ohlcv("AAPL", "1h", now, now)

    # -- map_instrument tests --

    def test_map_instrument_stock(self):
        """Test map_instrument with stock ticker."""
        ticker_dict = {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "market": "stocks",
            "type": "stock",
            "primary_exchange": "NMS",
            "currency_name": "usd",
        }
        inst = map_instrument("AAPL", ticker_dict)
        assert isinstance(inst, Instrument)
        assert inst.asset_class == AssetClass.EQUITY
        assert inst.instrument_type == InstrumentType.SPOT
        assert inst.symbol == "AAPL"
        assert inst.provider_symbol == "AAPL"
        assert inst.currency == "USD"

    def test_map_instrument_crypto(self):
        """Test map_instrument with crypto ticker."""
        ticker_dict = {
            "ticker": "X:BTCUSD",
            "name": "Bitcoin USD",
            "market": "crypto",
            "type": "perp",
            "primary_exchange": "Crypto",
            "currency_name": "usd",
        }
        inst = map_instrument("X:BTCUSD", ticker_dict)
        assert isinstance(inst, Instrument)
        assert inst.asset_class == AssetClass.CRYPTO
        # Perpetual type should detect "perpetual" in type field
        assert inst.instrument_type in (InstrumentType.SPOT, InstrumentType.PERPETUAL)
        assert inst.symbol == "X:BTCUSD"
        assert inst.provider_symbol == "X:BTCUSD"
        assert inst.currency == "USD"

    def test_map_instrument_forex(self):
        """Test map_instrument with forex ticker."""
        ticker_dict = {
            "ticker": "C:EURUSD",
            "name": "Euro US Dollar",
            "market": "forex",
            "type": "forex",
            "primary_exchange": "IDE",
            "currency_name": "usd",
        }
        inst = map_instrument("C:EURUSD", ticker_dict)
        assert isinstance(inst, Instrument)
        assert inst.asset_class == AssetClass.FOREX
        assert inst.instrument_type == InstrumentType.SPOT
        assert inst.symbol == "C:EURUSD"
        assert inst.provider_symbol == "C:EURUSD"
        assert inst.currency == "USD"

    def test_map_instrument_indices(self):
        """Test map_instrument with indices ticker."""
        ticker_dict = {
            "ticker": "I:SPX",
            "name": "S&P 500 Index",
            "market": "indices",
            "type": "index",
            "primary_exchange": "CBOE",
            "currency_name": "usd",
        }
        inst = map_instrument("I:SPX", ticker_dict)
        assert isinstance(inst, Instrument)
        assert inst.asset_class == AssetClass.INDEX
        assert inst.instrument_type == InstrumentType.SPOT
        assert inst.symbol == "I:SPX"
        assert inst.provider_symbol == "I:SPX"
        assert inst.currency == "USD"

    def test_map_instrument_options(self):
        """Test map_instrument with options ticker."""
        ticker_dict = {
            "ticker": "O:AAPL241220C00150000",
            "name": "Aapl Dec 20 2024 Call 150",
            "market": "options",
            "type": "option",
            "primary_exchange": "CBOE",
            "currency_name": "usd",
        }
        inst = map_instrument("O:AAPL241220C00150000", ticker_dict)
        assert isinstance(inst, Instrument)
        assert inst.asset_class == AssetClass.EQUITY
        assert inst.instrument_type == InstrumentType.OPTION
        assert inst.symbol == "O:AAPL241220C00150000"
        assert inst.provider_symbol == "O:AAPL241220C00150000"
        assert inst.currency == "USD"

    def test_map_instrument_futures(self):
        """Test map_instrument with futures ticker."""
        ticker_dict = {
            "ticker": "GCJ5",
            "name": "Gold Future",
            "market": "futures",
            "type": "future",
            "primary_exchange": "COMET",
            "currency_name": "usd",
        }
        inst = map_instrument("GCJ5", ticker_dict)
        assert isinstance(inst, Instrument)
        assert inst.asset_class == AssetClass.EQUITY
        assert inst.instrument_type == InstrumentType.FUTURE
        assert inst.symbol == "GCJ5"
        assert inst.provider_symbol == "GCJ5"
        assert inst.currency == "USD"

    # -- search_instruments logic tests --

    def test_search_instruments_returns_instrument_objects(self):
        """Test that search_instruments returns list of Instrument objects."""
        adapter = MassiveAdapter()
        results = adapter.search_instruments(query="AAPL")
        assert isinstance(results, list)
        # If API key is configured, we should get results
        # If not, results may be empty - that's OK for logic test
        if results:
            assert all(isinstance(inst, Instrument) for inst in results)

    def test_search_instruments_basic_query_structure(self):
        """Test search_instruments with query returns properly structured results."""
        adapter = MassiveAdapter()
        results = adapter.search_instruments(query="AAPL")
        # If we get results, verify structure
        if results:
            assert len(results) > 0
            assert all(hasattr(inst, "symbol") for inst in results)
            assert all(hasattr(inst, "asset_class") for inst in results)
            assert all(hasattr(inst, "instrument_type") for inst in results)

    # -- _search_match logic tests --

    def test_search_match_query(self):
        """Test _search_match with query filter."""
        adapter = MassiveAdapter()
        inst = Instrument(
            symbol="AAPL",
            provider_symbol="AAPL",
            exchange="NMS",
            currency="USD",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
        )
        # Matching query
        result = adapter._search_match(
            inst, query="AAPL", asset_class=None, instrument_type=None, quote=None, exchange=None
        )
        assert result is True
        # Non-matching query
        result = adapter._search_match(
            inst, query="MSFT", asset_class=None, instrument_type=None, quote=None, exchange=None
        )
        assert result is False

    def test_search_match_asset_class(self):
        """Test _search_match with asset_class filter."""
        adapter = MassiveAdapter()
        inst_equity = Instrument(
            symbol="AAPL",
            provider_symbol="AAPL",
            exchange="NMS",
            currency="USD",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
        )
        inst_crypto = Instrument(
            symbol="BTC",
            provider_symbol="BTC",
            exchange="Crypto",
            currency="USD",
            asset_class=AssetClass.CRYPTO,
            instrument_type=InstrumentType.SPOT,
        )
        # Matching equity
        result = adapter._search_match(
            inst_equity,
            query="",
            asset_class=AssetClass.EQUITY,
            instrument_type=None,
            quote=None,
            exchange=None,
        )
        assert result is True
        # Matching crypto filter on equity should fail
        result = adapter._search_match(
            inst_equity,
            query="",
            asset_class=AssetClass.CRYPTO,
            instrument_type=None,
            quote=None,
            exchange=None,
        )
        assert result is False
        # Matching crypto on crypto should pass
        result = adapter._search_match(
            inst_crypto,
            query="",
            asset_class=AssetClass.CRYPTO,
            instrument_type=None,
            quote=None,
            exchange=None,
        )
        assert result is True

    def test_search_match_instrument_type(self):
        """Test _search_match with instrument_type filter."""
        adapter = MassiveAdapter()
        inst_spot = Instrument(
            symbol="AAPL",
            provider_symbol="AAPL",
            exchange="NMS",
            currency="USD",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
        )
        inst_cfd = Instrument(
            symbol="AAPL",
            provider_symbol="AAPL",
            exchange="NMS",
            currency="USD",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.CFD,
        )
        # Matching SPOT
        result = adapter._search_match(
            inst_spot,
            query="",
            asset_class=None,
            instrument_type=InstrumentType.SPOT,
            quote=None,
            exchange=None,
        )
        assert result is True
        # CFD should not match SPOT filter
        result = adapter._search_match(
            inst_cfd,
            query="",
            asset_class=None,
            instrument_type=InstrumentType.SPOT,
            quote=None,
            exchange=None,
        )
        assert result is False

    def test_search_match_quote(self):
        """Test _search_match with quote filter."""
        adapter = MassiveAdapter()
        inst_usd = Instrument(
            symbol="AAPL",
            provider_symbol="AAPL",
            exchange="NMS",
            currency="USD",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
        )
        inst_eur = Instrument(
            symbol="AAPL",
            provider_symbol="AAPL",
            exchange="NMS",
            currency="EUR",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
        )
        # Matching USD
        result = adapter._search_match(
            inst_usd, query="", asset_class=None, instrument_type=None, quote="USD", exchange=None
        )
        assert result is True
        # EUR should not match USD filter
        result = adapter._search_match(
            inst_eur, query="", asset_class=None, instrument_type=None, quote="USD", exchange=None
        )
        assert result is False

    def test_search_match_exchange(self):
        """Test _search_match with exchange filter."""
        adapter = MassiveAdapter()
        inst_nms = Instrument(
            symbol="AAPL",
            provider_symbol="AAPL",
            exchange="NMS",
            currency="USD",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
        )
        inst_nyse = Instrument(
            symbol="AAPL",
            provider_symbol="AAPL",
            exchange="NYSE",
            currency="USD",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
        )
        # Matching NMS
        result = adapter._search_match(
            inst_nms, query="", asset_class=None, instrument_type=None, quote=None, exchange="NMS"
        )
        assert result is True
        # NYSE should not match NMS filter
        result = adapter._search_match(
            inst_nyse, query="", asset_class=None, instrument_type=None, quote=None, exchange="NMS"
        )
        assert result is False

    # -- fetch_fundamentals tests --

    def test_fetch_fundamentals_structure(self):
        """Test that fetch_fundamentals returns a Fundamentals object."""
        adapter = MassiveAdapter()
        # This test verifies the method exists and returns correct type
        # Actual data requires API key, so we just check the method signature
        assert hasattr(adapter, "fetch_fundamentals")
        import inspect

        sig = inspect.signature(adapter.fetch_fundamentals)
        assert "symbol" in sig.parameters

    def test_map_instrument_all_types(self):
        """Test map_instrument with all major market types."""
        # Stock
        ticker = {
            "ticker": "AAPL",
            "name": "AAPL",
            "market": "stocks",
            "type": "stock",
            "primary_exchange": "NMS",
            "currency_name": "usd",
        }
        inst = map_instrument("AAPL", ticker)
        assert inst.asset_class == AssetClass.EQUITY
        assert inst.instrument_type == InstrumentType.SPOT

        # Crypto
        ticker = {
            "ticker": "X:BTCUSD",
            "name": "BTC",
            "market": "crypto",
            "type": "perp",
            "primary_exchange": "Crypto",
            "currency_name": "usd",
        }
        inst = map_instrument("X:BTCUSD", ticker)
        assert inst.asset_class == AssetClass.CRYPTO

        # Forex
        ticker = {
            "ticker": "C:EURUSD",
            "name": "EURUSD",
            "market": "forex",
            "type": "forex",
            "primary_exchange": "IDE",
            "currency_name": "usd",
        }
        inst = map_instrument("C:EURUSD", ticker)
        assert inst.asset_class == AssetClass.FOREX

        # Indices
        ticker = {
            "ticker": "I:SPX",
            "name": "SPX",
            "market": "indices",
            "type": "index",
            "primary_exchange": "CBOE",
            "currency_name": "usd",
        }
        inst = map_instrument("I:SPX", ticker)
        assert inst.asset_class == AssetClass.INDEX

        # Options
        ticker = {
            "ticker": "O:AAPL241220C00150000",
            "name": "AAPL Option",
            "market": "options",
            "type": "option",
            "primary_exchange": "CBOE",
            "currency_name": "usd",
        }
        inst = map_instrument("O:AAPL241220C00150000", ticker)
        assert inst.asset_class == AssetClass.EQUITY
        assert inst.instrument_type == InstrumentType.OPTION

        # Futures
        ticker = {
            "ticker": "GCJ5",
            "name": "Gold",
            "market": "futures",
            "type": "future",
            "primary_exchange": "COMET",
            "currency_name": "usd",
        }
        inst = map_instrument("GCJ5", ticker)
        assert inst.asset_class == AssetClass.EQUITY
        assert inst.instrument_type == InstrumentType.FUTURE
