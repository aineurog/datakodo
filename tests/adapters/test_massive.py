"""Massive adapter tests."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from datakodo.adapters.massive.adapter import MassiveAdapter
from datakodo.adapters.massive.mapper import map_instrument
from datakodo.core.enums import AssetClass, InstrumentType
from datakodo.core.exceptions import AuthenticationError
from datakodo.core.exceptions import ConnectionError as DataConnectionError
from datakodo.core.instruments import Instrument


class TestMassiveAdapter:
    def test_adapter_capabilities(self):
        adapter = MassiveAdapter()
        assert adapter.supports_ohlcv is True
        assert adapter.supports_streaming_orderbook is False
        assert adapter.supports_fundamentals is True

    def test_fetch_ohlcv_mocked(self, monkeypatch):
        """fetch_ohlcv maps mocked aggs to the canonical frame (offline)."""
        adapter = MassiveAdapter(api_key="test-key")
        now = datetime.now(UTC)
        raw = [
            {
                "t": int((now - timedelta(hours=3)).timestamp() * 1000),
                "o": 100.0,
                "h": 101.0,
                "l": 99.0,
                "c": 100.5,
                "v": 1000.0,
                "vw": 100.2,
                "n": 10,
                "otc": None,
            },
            {
                "t": int((now - timedelta(hours=2)).timestamp() * 1000),
                "o": 100.5,
                "h": 102.0,
                "l": 100.0,
                "c": 101.5,
                "v": 2000.0,
                "vw": 101.0,
                "n": 20,
                "otc": None,
            },
        ]
        monkeypatch.setattr(adapter._rest, "aggs", lambda *a, **k: raw)
        df = adapter.fetch_ohlcv("AAPL", "1h", now - timedelta(hours=4), now)
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "is_closed",
        ]
        assert len(df) == 2
        assert df["is_closed"].all()
        assert df.iloc[0]["open"] == 100.0
        assert df.iloc[-1]["close"] == 101.5

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

    def _fake_tickers(self):
        """Canned ticker reference rows (offline, no API key needed)."""
        return [
            {
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "market": "stocks",
                "type": "stock",
                "primary_exchange": "NMS",
                "currency_name": "usd",
                "active": True,
            },
            {
                "ticker": "MSFT",
                "name": "Microsoft Corp.",
                "market": "stocks",
                "type": "stock",
                "primary_exchange": "NMS",
                "currency_name": "usd",
                "active": True,
            },
        ]

    def test_search_instruments_returns_instrument_objects(self, monkeypatch):
        """Test that search_instruments returns list of Instrument objects."""
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "list_tickers", lambda *a, **k: self._fake_tickers())
        results = adapter.search_instruments(query="AAPL")
        assert isinstance(results, list)
        assert len(results) > 0
        assert all(isinstance(inst, Instrument) for inst in results)

    def test_search_instruments_basic_query_structure(self, monkeypatch):
        """Test search_instruments with query returns properly structured results."""
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "list_tickers", lambda *a, **k: self._fake_tickers())
        results = adapter.search_instruments(query="AAPL")
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


class TestMassiveAdapterStream:
    def test_stream_trades_passes_through_mapped_trades(self, monkeypatch):
        """Adapter yields ws Trades untouched (mapped once, in ``ws``)."""
        from datetime import datetime

        from datakodo.core.schemas import Trade

        expected = Trade(
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            price=150.0,
            size=10.0,
            side=None,
            trade_id=1,
        )

        class _FakeWS:
            async def trade_stream(self, symbol):
                assert symbol == "AAPL"
                yield expected

        adapter = MassiveAdapter(api_key="test-key")
        adapter._ws = _FakeWS()

        async def collect():
            return [t async for t in adapter.stream_trades("AAPL")]

        trades = asyncio.run(collect())
        assert trades == [expected]


# --- MassiveWS (mocked transport, offline) ---------------------------------


class _FakeWSClient:
    """Stand-in for ``massive.WebSocketClient`` (no network)."""

    fail_auth = False

    def __init__(self, api_key=None, market="stocks", raw=False, max_reconnects=5, **kwargs):
        self.api_key = api_key
        self.market = market
        self.subscribed: list = []
        self.unsubscribed: list = []
        self.handler = None
        self.closed = False

    async def connect(self, handler, *args, **kwargs):
        if self.fail_auth:
            from massive.websocket import AuthError

            raise AuthError("bad key")
        self.handler = handler
        await asyncio.Event().wait()

    async def subscribe(self, *subs):
        self.subscribed.extend(subs)

    async def unsubscribe(self, *subs):
        self.unsubscribed.extend(subs)

    async def close(self):
        self.closed = True


def _make_ws(monkeypatch, **kwargs):
    monkeypatch.setattr("datakodo.adapters.massive.ws.WebSocketClient", _FakeWSClient)
    from datakodo.adapters.massive.ws import MassiveWS

    return MassiveWS(api_key="test-key", **kwargs)


class TestMassiveWS:
    def test_market_routing(self):
        from datakodo.adapters.massive.ws import MassiveWS

        assert MassiveWS._market_for("AAPL") == "stocks"
        assert MassiveWS._market_for("X:BTCUSD") == "crypto"
        assert MassiveWS._market_for("C:EURUSD") == "forex"
        assert MassiveWS._market_for("I:SPX") == "indices"
        assert MassiveWS._market_for("O:AAPL1") == "options"

    def test_trade_stream_yields_mapped_trades(self, monkeypatch):
        ws = _make_ws(monkeypatch)

        async def collect():
            out = []
            gen = ws.trade_stream("AAPL")
            try:
                async for trade in gen:
                    out.append(trade)
                    if len(out) == 2:
                        break
            finally:
                await gen.aclose()
            return out

        async def drive():
            task = asyncio.create_task(collect())
            for _ in range(100):
                await asyncio.sleep(0.01)
                if ws._client is not None and ws._client.handler is not None:
                    break
            fake = ws._client
            import json as _json

            frames = [
                {"ev": "status", "status": "connected"},
                {"ev": "T", "sym": "AAPL", "p": 150.0, "s": 10, "t": 1700000000000, "i": 1},
                "not json",
                {"ev": "T", "sym": "AAPL", "p": 151.0, "s": 5, "t": 1700000001000, "i": 2},
                {"ev": "T", "sym": "AAPL", "s": 5},  # no price -> skipped
            ]
            for frame in frames:
                await fake.handler(frame if isinstance(frame, str) else _json.dumps(frame))
            trades = await asyncio.wait_for(task, 5)
            subs = list(fake.subscribed)
            unsubs = list(fake.unsubscribed)
            await ws.disconnect()
            return trades, subs, unsubs

        trades, subs, unsubs = asyncio.run(drive())
        assert [t.price for t in trades] == [150.0, 151.0]
        assert [t.size for t in trades] == [10.0, 5.0]
        assert subs == ["T.AAPL"]
        assert unsubs == ["T.AAPL"]

    def test_trade_stream_survives_string_trade_id(self, monkeypatch):
        """A non-numeric trade id maps to None instead of killing the stream."""
        ws = _make_ws(monkeypatch)

        async def collect():
            out = []
            gen = ws.trade_stream("X:BTCUSD")
            try:
                async for trade in gen:
                    out.append(trade)
                    if len(out) == 2:
                        break
            finally:
                await gen.aclose()
            return out

        async def drive():
            task = asyncio.create_task(collect())
            for _ in range(100):
                await asyncio.sleep(0.01)
                if ws._client is not None and ws._client.handler is not None:
                    break
            fake = ws._client
            import json as _json

            await fake.handler(
                _json.dumps(
                    {
                        "ev": "XT",
                        "sym": "X:BTCUSD",
                        "p": 70000.0,
                        "s": 0.5,
                        "t": 1700000000000,
                        "i": "abc123",
                        "c": 2,
                    }
                )
            )
            await fake.handler(
                _json.dumps(
                    {
                        "ev": "XT",
                        "sym": "X:BTCUSD",
                        "p": 70001.0,
                        "s": 0.1,
                        "t": 1700000001000,
                        "i": 456,
                        "c": 1,
                    }
                )
            )
            trades = await asyncio.wait_for(task, 5)
            await ws.disconnect()
            return trades

        trades = asyncio.run(drive())
        assert [t.trade_id for t in trades] == [None, 456]
        assert [t.side for t in trades] == ["buy", "sell"]

    def test_trade_stream_drops_stale_queued_frames(self, monkeypatch):
        """Frames queued by a previous symbol never leak into the next stream."""
        ws = _make_ws(monkeypatch)
        ws._queue.put_nowait(
            {"ev": "T", "sym": "AAPL", "p": 1.0, "s": 1, "t": 1700000000000, "i": 1}
        )

        async def collect():
            out = []
            gen = ws.trade_stream("X:BTCUSD")
            try:
                async for trade in gen:
                    out.append(trade)
                    if len(out) == 1:
                        break
            finally:
                await gen.aclose()
            return out

        async def drive():
            task = asyncio.create_task(collect())
            for _ in range(100):
                await asyncio.sleep(0.01)
                if ws._client is not None and ws._client.handler is not None:
                    break
            fake = ws._client
            import json as _json

            await fake.handler(
                _json.dumps(
                    {
                        "ev": "XT",
                        "sym": "X:BTCUSD",
                        "p": 70000.0,
                        "s": 0.5,
                        "t": 1700000002000,
                        "i": 7,
                    }
                )
            )
            trades = await asyncio.wait_for(task, 5)
            await ws.disconnect()
            return trades

        trades = asyncio.run(drive())
        assert len(trades) == 1
        assert trades[0].price == 70000.0

    def test_connect_auth_failure_maps(self, monkeypatch):
        ws = _make_ws(monkeypatch)
        monkeypatch.setattr(_FakeWSClient, "fail_auth", True)

        async def go():
            with pytest.raises(AuthenticationError):
                await ws.connect("stocks")

        try:
            asyncio.run(go())
        finally:
            monkeypatch.setattr(_FakeWSClient, "fail_auth", False)

    def test_disconnect_and_not_connected_guard(self, monkeypatch):
        ws = _make_ws(monkeypatch)

        async def go():
            with pytest.raises(DataConnectionError):
                await ws.subscribe("T", "AAPL")
            await ws.connect("stocks")
            assert ws.connected
            await ws.disconnect()
            assert not ws.connected

        asyncio.run(go())

    def test_trade_channel_routing(self):
        from datakodo.adapters.massive.ws import MassiveWS
        from datakodo.core.exceptions import NotSupportedError

        assert MassiveWS._trade_channel_for("AAPL") == "T"
        assert MassiveWS._trade_channel_for("X:BTCUSD") == "XT"
        assert MassiveWS._trade_channel_for("O:AAPL1") == "T"
        with pytest.raises(NotSupportedError):
            MassiveWS._trade_channel_for("C:EURUSD")
        with pytest.raises(NotSupportedError):
            MassiveWS._trade_channel_for("I:SPX")

    def test_trade_stream_uses_xt_channel_for_crypto(self, monkeypatch):
        ws = _make_ws(monkeypatch)

        async def collect():
            out = []
            gen = ws.trade_stream("X:BTCUSD")
            try:
                async for trade in gen:
                    out.append(trade)
                    if len(out) == 1:
                        break
            finally:
                await gen.aclose()
            return out

        async def drive():
            task = asyncio.create_task(collect())
            for _ in range(100):
                await asyncio.sleep(0.01)
                if ws._client is not None and ws._client.handler is not None:
                    break
            fake = ws._client
            import json as _json

            await fake.handler(
                _json.dumps(
                    {
                        "ev": "XT",
                        "pair": "BTC-USD",
                        "p": 70000.0,
                        "s": 0.5,
                        "t": 1700000000000,
                        "i": 7,
                        "c": [2],
                    }
                )
            )
            trades = await asyncio.wait_for(task, 5)
            subs = list(fake.subscribed)
            await ws.disconnect()
            return trades, subs

        trades, subs = asyncio.run(drive())
        assert trades[0].side == "buy"
        assert subs == ["XT.X:BTCUSD"]

    def test_trade_stream_rejects_forex_and_indices(self, monkeypatch):
        from datakodo.core.exceptions import NotSupportedError

        ws = _make_ws(monkeypatch)

        async def go():
            with pytest.raises(NotSupportedError):
                async for _ in ws.trade_stream("C:EURUSD"):
                    pass
            with pytest.raises(NotSupportedError):
                async for _ in ws.trade_stream("I:SPX"):
                    pass

        asyncio.run(go())


class TestMassiveFinalTicks:
    def test_map_trades_list_conditions(self):
        from datakodo.adapters.massive.mapper import map_trades

        buy = map_trades({"t": 1700000000000, "p": 10.0, "s": 1.0, "c": [2], "i": 1})
        assert buy.side == "buy"
        sell = map_trades({"t": 1700000000000, "p": 10.0, "s": 1.0, "c": [1], "i": 2})
        assert sell.side == "sell"
        unknown = map_trades({"t": 1700000000000, "p": 10.0, "s": 1.0, "c": [37], "i": 3})
        assert unknown.side is None

    def test_fetch_ohlcv_forwards_adjust(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        adapter = MassiveAdapter(api_key="test-key")
        now = datetime.now(UTC)
        seen: dict = {}

        def _fake_aggs(symbol, timeframe, start, end, adjust=True, limit=5000):
            seen["adjust"] = adjust
            return [
                {
                    "t": int((now - timedelta(hours=2)).timestamp() * 1000),
                    "o": 1.0,
                    "h": 2.0,
                    "l": 0.5,
                    "c": 1.5,
                    "v": 100.0,
                    "vw": 1.2,
                    "n": 5,
                }
            ]

        monkeypatch.setattr(adapter._rest, "aggs", _fake_aggs)
        adapter.fetch_ohlcv("AAPL", "1h", now - timedelta(hours=3), now, adjust=False)
        assert seen["adjust"] is False

    def test_search_accepts_massive_spellings(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        tickers = [
            {
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "market": "stocks",
                "type": "stock",
                "primary_exchange": "NMS",
                "currency_name": "usd",
            },
        ]
        monkeypatch.setattr(adapter._rest, "list_tickers", lambda *a, **k: tickers)
        # Canonical enum and Massive string spellings both match EQUITY.
        assert len(adapter.search_instruments("apple", asset_class=AssetClass.EQUITY)) == 1
        assert len(adapter.search_instruments("apple", asset_class="stocks")) == 1
        assert len(adapter.search_instruments("apple", asset_class="indices")) == 0

    def test_search_matches_name(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        tickers = [
            {
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "market": "stocks",
                "type": "stock",
                "primary_exchange": "NMS",
                "currency_name": "usd",
            },
        ]
        monkeypatch.setattr(adapter._rest, "list_tickers", lambda *a, **k: tickers)
        assert len(adapter.search_instruments("apple")) == 1
        assert len(adapter.search_instruments("AAPL")) == 1
        assert len(adapter.search_instruments("zzz-no-match")) == 0


# --- MassiveREST (offline, mocked transport) ---------------------------------


class _FakeResp:
    """Minimal stand-in for a urllib3 response."""

    def __init__(self, status, data=b"", headers=None):
        self.status = status
        self.data = data
        self.headers = headers or {}


class _FakeHTTP:
    """Scripted HTTP transport: pops responses or raises staged exceptions."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def request(self, method, url, fields=None, headers=None, retries=False):
        self.calls.append({"method": method, "url": url, "fields": fields})
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _make_rest(monkeypatch, max_retries=2):
    """Build a MassiveREST with recorded no-op sleeps (no real waiting)."""
    from datakodo.adapters.massive.config import MassiveConfig
    from datakodo.adapters.massive.rest import MassiveREST
    from datakodo.core.config import Config

    sleeps: list = []
    monkeypatch.setattr("datakodo.adapters.massive.rest.time.sleep", lambda s: sleeps.append(s))
    rest = MassiveREST(
        massive_config=MassiveConfig(api_key="test-key"),
        config=Config(max_retries=max_retries, retry_base_delay=0.0),
    )
    return rest, sleeps


class TestMassiveResolution:
    def test_all_nine_timeframes(self):
        from datakodo.adapters.massive.rest import massive_resolution
        from datakodo.core.enums import Timeframe

        expected = {
            "1m": (1, "minute"),
            "5m": (5, "minute"),
            "15m": (15, "minute"),
            "30m": (30, "minute"),
            "1h": (1, "hour"),
            "4h": (4, "hour"),
            "1d": (1, "day"),
            "1w": (1, "week"),
            "1mo": (1, "month"),
        }
        for tf, resolution in expected.items():
            assert massive_resolution(tf) == resolution
            assert massive_resolution(Timeframe(tf)) == resolution

    def test_unknown_timeframe_raises(self):
        from datakodo.adapters.massive.rest import massive_resolution

        with pytest.raises(ValueError, match="Unknown timeframe"):
            massive_resolution("9m")


class TestToMillis:
    def test_naive_assumed_utc(self):
        from datakodo.adapters.massive.rest import _to_millis

        naive = datetime(2026, 1, 1, 12, 0, 0)
        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert _to_millis(naive) == _to_millis(aware) == 1767268800000

    def test_aware_offset_converted(self):
        from datetime import timezone

        from datakodo.adapters.massive.rest import _to_millis

        plus2 = datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        assert _to_millis(plus2) == 1767268800000


class TestServerMessage:
    @staticmethod
    def _msg(resp):
        from datakodo.adapters.massive.rest import MassiveREST

        return MassiveREST._server_message(resp)

    def test_extracts_message(self):
        import json as _json

        body = _json.dumps({"message": "Tick data needs paid plan", "request_id": "x"})
        assert self._msg(_FakeResp(403, body.encode())) == "Tick data needs paid plan"

    def test_invalid_json_returns_none(self):
        assert self._msg(_FakeResp(500, b"not json")) is None

    def test_non_bytes_returns_none(self):
        assert self._msg(_FakeResp(500, "oops")) is None

    def test_missing_message_returns_none(self):
        assert self._msg(_FakeResp(500, b'{"status": "ERROR"}')) is None


class TestTranslateResponse:
    def test_400(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import DataValidationError

        err = rest._translate_response(_FakeResp(400, b'{"message": "bad request"}'))
        assert isinstance(err, DataValidationError)
        assert "bad request" in str(err)

    def test_401(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import AuthenticationError

        err = rest._translate_response(_FakeResp(401, b""))
        assert isinstance(err, AuthenticationError)
        assert "MASSIVE_API_KEY" in str(err)

    def test_403_paid_tier(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import PaidTierRequiredError

        err = rest._translate_response(_FakeResp(403, b'{"message": "upgrade needed"}'))
        assert isinstance(err, PaidTierRequiredError)
        assert "pricing" in str(err) and "upgrade needed" in str(err)

    def test_404(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import SymbolNotFoundError

        assert isinstance(rest._translate_response(_FakeResp(404, b"")), SymbolNotFoundError)

    def test_429_retry_after(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import RateLimitError

        err = rest._translate_response(_FakeResp(429, b"", {"Retry-After": "7"}))
        assert isinstance(err, RateLimitError)
        assert err.retry_after == 7.0

    def test_429_no_header(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import RateLimitError

        err = rest._translate_response(_FakeResp(429, b""))
        assert isinstance(err, RateLimitError)
        assert err.retry_after == 0.0

    def test_429_bad_header(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import RateLimitError

        err = rest._translate_response(_FakeResp(429, b"", {"Retry-After": "soon"}))
        assert isinstance(err, RateLimitError)
        assert err.retry_after == 0.0

    def test_500_generic(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import ProviderError

        err = rest._translate_response(_FakeResp(503, b'{"message": "down"}'))
        assert isinstance(err, ProviderError)
        assert "503" in str(err) and "down" in str(err)


class TestMassiveRESTGet:
    @staticmethod
    def _ok(results):
        import json as _json

        return _FakeResp(200, _json.dumps({"results": results}).encode())

    def test_200_result_key(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        rest.client = _FakeHTTP([self._ok([{"a": 1}])])
        assert rest._get("/v3/reference/tickers", result_key="results") == [{"a": 1}]

    def test_200_raw(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        resp = self._ok([1])
        rest.client = _FakeHTTP([resp])
        assert rest._get("/x", raw=True) is resp

    def test_200_bad_json_returns_empty(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        rest.client = _FakeHTTP([_FakeResp(200, b"nope")])
        assert rest._get("/x", result_key="results") == []

    def test_200_missing_key_returns_empty(self, monkeypatch):
        import json as _json

        rest, _ = _make_rest(monkeypatch)
        rest.client = _FakeHTTP([_FakeResp(200, _json.dumps({"foo": 1}).encode())])
        assert rest._get("/x", result_key="results") == []

    def test_200_deserializer(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        rest.client = _FakeHTTP([self._ok([{"a": 1}, {"a": 2}])])
        out = rest._get("/x", result_key="results", deserializer=lambda o: o["a"])
        assert out == [1, 2]

    def test_401_no_retry(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import AuthenticationError

        rest.client = _FakeHTTP([_FakeResp(401, b"")])
        with pytest.raises(AuthenticationError):
            rest._get("/x")
        assert len(rest.client.calls) == 1

    def test_404_no_retry(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import SymbolNotFoundError

        rest.client = _FakeHTTP([_FakeResp(404, b"")])
        with pytest.raises(SymbolNotFoundError):
            rest._get("/x")

    def test_429_then_success(self, monkeypatch):
        rest, sleeps = _make_rest(monkeypatch)
        rest.client = _FakeHTTP([_FakeResp(429, b"", {"Retry-After": "2"}), self._ok([1])])
        assert rest._get("/x", result_key="results") == [1]
        assert len(rest.client.calls) == 2
        assert sleeps == [2.0]

    def test_429_exhausted(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import RetriesExhaustedError

        rest.client = _FakeHTTP([_FakeResp(429, b"")] * 3)
        with pytest.raises(RetriesExhaustedError):
            rest._get("/x")
        assert len(rest.client.calls) == 3  # initial + 2 retries

    def test_500_exhausted(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        from datakodo.core.exceptions import RetriesExhaustedError

        rest.client = _FakeHTTP([_FakeResp(500, b"e")] * 3)
        with pytest.raises(RetriesExhaustedError):
            rest._get("/x")

    def test_timeout_then_success(self, monkeypatch):
        from urllib3.exceptions import TimeoutError as Urllib3TimeoutError

        rest, _ = _make_rest(monkeypatch)
        rest.client = _FakeHTTP([Urllib3TimeoutError("slow"), self._ok([9])])
        assert rest._get("/x", result_key="results") == [9]

    def test_timeout_exhausted_stays_distinct(self, monkeypatch):
        from urllib3.exceptions import TimeoutError as Urllib3TimeoutError

        from datakodo.core.exceptions import TimeoutError as DataTimeoutError

        rest, _ = _make_rest(monkeypatch)
        rest.client = _FakeHTTP([Urllib3TimeoutError("slow")] * 3)
        with pytest.raises(DataTimeoutError):
            rest._get("/x")

    def test_connection_error(self, monkeypatch):
        from urllib3.exceptions import HTTPError as Urllib3HTTPError

        rest, _ = _make_rest(monkeypatch, max_retries=0)
        rest.client = _FakeHTTP([Urllib3HTTPError("boom")])
        with pytest.raises(DataConnectionError):
            rest._get("/x")

    def test_local_limiter_exhausted(self, monkeypatch):
        rest, _ = _make_rest(monkeypatch, max_retries=0)
        from datakodo.core.exceptions import RetriesExhaustedError

        monkeypatch.setattr(rest._limiter, "consume", lambda weight=1: False)
        monkeypatch.setattr(rest._limiter, "wait_time", lambda weight=1: 1.5)
        with pytest.raises(RetriesExhaustedError, match="local rate limit"):
            rest._get("/x")


class TestMassiveRESTEndpoints:
    @staticmethod
    def _rest(monkeypatch):
        rest, _ = _make_rest(monkeypatch)
        return rest

    def test_aggs_rows_and_params(self, monkeypatch):
        from types import SimpleNamespace

        rest = self._rest(monkeypatch)
        seen = {}

        def _fake_list_aggs(**kwargs):
            seen.update(kwargs)
            return [
                SimpleNamespace(
                    timestamp=1700000000000,
                    open=1.0,
                    high=2.0,
                    low=0.5,
                    close=1.5,
                    volume=100.0,
                    vwap=1.2,
                    transactions=5,
                    otc=False,
                )
            ]

        monkeypatch.setattr(rest, "list_aggs", _fake_list_aggs)
        rows = rest.aggs(
            "AAPL", "1h", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
        )
        assert rows == [
            {
                "t": 1700000000000,
                "o": 1.0,
                "h": 2.0,
                "l": 0.5,
                "c": 1.5,
                "v": 100.0,
                "vw": 1.2,
                "n": 5,
                "otc": False,
            }
        ]
        assert seen["ticker"] == "AAPL"
        assert (seen["multiplier"], seen["timespan"]) == (1, "hour")
        assert seen["sort"] == "asc" and seen["adjusted"] is True

    def test_aggs_limit_clamped(self, monkeypatch):
        rest = self._rest(monkeypatch)
        seen = {}

        def _fake_list_aggs(**kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr(rest, "list_aggs", _fake_list_aggs)
        rest.aggs(
            "AAPL",
            "1d",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 2, 1, tzinfo=UTC),
            limit=100_000,
        )
        assert seen["limit"] == 50_000

    def test_aggs_bad_timeframe(self, monkeypatch):
        rest = self._rest(monkeypatch)
        from datakodo.core.exceptions import InvalidTimeframeError

        with pytest.raises(InvalidTimeframeError):
            rest.aggs(
                "AAPL", "9m", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
            )

    def test_list_tickers_forwards_params(self, monkeypatch):
        from types import SimpleNamespace

        rest = self._rest(monkeypatch)
        seen = {}

        def _fake_parent_list_tickers(self, **kwargs):
            seen.update(kwargs)
            return [SimpleNamespace(ticker="AAPL", market="stocks")]

        monkeypatch.setattr("massive.rest.RESTClient.list_tickers", _fake_parent_list_tickers)
        out = rest.list_tickers(market="stocks", search="app", currency="USD", limit=5000)
        assert out == [{"ticker": "AAPL", "market": "stocks"}]
        assert seen["market"] == "stocks" and seen["search"] == "app"
        assert seen["limit"] == 1000  # clamped
        assert seen["params"] == {"currency": "USD"}

    def test_ticker_details(self, monkeypatch):
        from types import SimpleNamespace

        rest = self._rest(monkeypatch)
        monkeypatch.setattr(
            rest,
            "get_ticker_details",
            lambda **k: SimpleNamespace(name="Apple Inc.", market="stocks"),
        )
        assert rest.ticker_details("AAPL") == {"name": "Apple Inc.", "market": "stocks"}

    def test_ticker_details_empty(self, monkeypatch):
        rest = self._rest(monkeypatch)
        monkeypatch.setattr(rest, "get_ticker_details", lambda **k: None)
        assert rest.ticker_details("NOPE") == {}

    def test_list_trades_params(self, monkeypatch):
        from types import SimpleNamespace

        rest = self._rest(monkeypatch)
        seen = {}

        def _fake_parent_list_trades(self, **kwargs):
            seen.update(kwargs)
            return [SimpleNamespace(price=1.0)]

        monkeypatch.setattr("massive.rest.RESTClient.list_trades", _fake_parent_list_trades)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 2, tzinfo=UTC)
        out = rest.list_trades("AAPL", start, end, limit=99_999)
        assert out == [{"price": 1.0}]
        assert seen["timestamp_gte"] == 1767225600000
        assert seen["timestamp_lte"] == 1767312000000
        assert seen["limit"] == 50_000
        assert seen["sort"] == "timestamp" and seen["order"] == "asc"

    def test_ensure_symbol_known_strips(self, monkeypatch):
        rest = self._rest(monkeypatch)
        assert rest.ensure_symbol_known("  AAPL ") == "AAPL"


# --- MassiveAdapter behaviour -------------------------------------------------


class TestMassiveAdapterHelpers:
    def test_market_of_prefixes(self):
        adapter = MassiveAdapter(api_key="test-key")
        assert adapter._market_of("AAPL") == "stocks"
        assert adapter._market_of("X:BTCUSD") == "crypto"
        assert adapter._market_of("C:EURUSD") == "forex"
        assert adapter._market_of("I:SPX") == "index"
        assert adapter._market_of("O:AAPL1") == "options"
        assert adapter._market_of("aapl") == "stocks"

    def test_massive_market_of(self):
        import datakodo.adapters.massive.adapter as _mod

        assert _mod._massive_market_of(None) is None
        assert _mod._massive_market_of(AssetClass.EQUITY) == "stocks"
        assert _mod._massive_market_of(AssetClass.CRYPTO) == "crypto"
        assert _mod._massive_market_of("stocks") == "stocks"
        assert _mod._massive_market_of("indices") == "indices"
        assert _mod._massive_market_of(AssetClass.INDEX) == "indices"

    def test_massive_type_of(self):
        import datakodo.adapters.massive.adapter as _mod

        assert _mod._massive_type_of(None) is None
        assert _mod._massive_type_of(InstrumentType.OPTION) == "option"
        assert _mod._massive_type_of(InstrumentType.FUTURE) == "future"
        assert _mod._massive_type_of(InstrumentType.SPOT) is None
        assert _mod._massive_type_of(InstrumentType.PERPETUAL) is None

    def test_map_asset_class_from_market(self):
        adapter = MassiveAdapter(api_key="test-key")
        assert adapter._map_asset_class_from_market("stocks") == AssetClass.EQUITY
        assert adapter._map_asset_class_from_market("crypto") == AssetClass.CRYPTO
        assert adapter._map_asset_class_from_market("forex") == AssetClass.FOREX
        assert adapter._map_asset_class_from_market("indices") == AssetClass.INDEX
        assert adapter._map_asset_class_from_market("options") == AssetClass.EQUITY
        assert adapter._map_asset_class_from_market("futures") == AssetClass.EQUITY
        assert adapter._map_asset_class_from_market(None) is None
        assert adapter._map_asset_class_from_market("weird") is None

    def test_map_instrument_type_from_market(self):
        adapter = MassiveAdapter(api_key="test-key")
        assert adapter._map_instrument_type_from_market("stocks") == InstrumentType.SPOT
        assert adapter._map_instrument_type_from_market("options") == InstrumentType.OPTION
        assert adapter._map_instrument_type_from_market("futures") == InstrumentType.FUTURE
        assert adapter._map_instrument_type_from_market(None) is None
        assert adapter._map_instrument_type_from_market("weird") is None

    def test_parse_time_to_utc(self):
        adapter = MassiveAdapter(api_key="test-key")
        expected = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
        assert adapter._parse_time_to_utc(1700000000000) == expected
        assert adapter._parse_time_to_utc(1700000000000000000) == expected
        assert adapter._parse_time_to_utc(None) is None
        assert adapter._parse_time_to_utc(0) is None


class TestFetchFundamentals:
    @staticmethod
    def _details():
        return {
            "name": "Apple Inc.",
            "market": "stocks",
            "currency_name": "USD",
            "primary_exchange": "NMS",
            "tick": {"t": 1700000000000},
        }

    def test_happy_path(self, monkeypatch):
        from datakodo.core.schemas import Fundamentals

        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "ticker_details", lambda t: self._details())
        out = adapter.fetch_fundamentals("AAPL")
        assert isinstance(out, Fundamentals)
        assert out.symbol == "AAPL"
        assert out.name == "Apple Inc."
        assert out.asset_class == AssetClass.EQUITY
        assert out.instrument_type == InstrumentType.SPOT
        assert out.currency == "USD"
        assert out.exchange == "NMS"
        assert out.as_of == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)

    def test_accepts_instrument(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "ticker_details", lambda t: self._details())
        inst = Instrument(
            symbol="AAPL",
            provider_symbol="AAPL",
            exchange="NMS",
            currency="USD",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
        )
        assert adapter.fetch_fundamentals(inst).symbol == "AAPL"

    def test_unknown_symbol(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "ticker_details", lambda t: {})
        from datakodo.core.exceptions import SymbolNotFoundError

        with pytest.raises(SymbolNotFoundError):
            adapter.fetch_fundamentals("NOPE")

    def test_no_tick_as_of_none(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        details = self._details()
        del details["tick"]
        monkeypatch.setattr(adapter._rest, "ticker_details", lambda t: details)
        assert adapter.fetch_fundamentals("AAPL").as_of is None

    def test_crypto_mapping_ns_tick(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        details = {
            "name": "Bitcoin",
            "market": "crypto",
            "currency_name": "USD",
            "primary_exchange": "CBSE",
            "tick": {"t": 1700000000000000000},
        }
        monkeypatch.setattr(adapter._rest, "ticker_details", lambda t: details)
        out = adapter.fetch_fundamentals("X:BTCUSD")
        assert out.asset_class == AssetClass.CRYPTO
        assert out.as_of == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)


class TestFetchTicks:
    def test_maps_rest_trades(self, monkeypatch):
        from datakodo.core.schemas import Trade

        adapter = MassiveAdapter(api_key="test-key")
        raw = [
            {"price": 150.0, "size": 10.0, "sip_timestamp": 1700000000000000000, "id": 7},
            {"price": 151.0, "size": 5.0, "t": 1700000001000, "id": "abc"},
        ]
        monkeypatch.setattr(adapter._rest, "list_trades", lambda *a, **k: raw)
        out = adapter.fetch_ticks(
            "AAPL", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC), limit=2
        )
        assert len(out) == 2
        assert all(isinstance(t, Trade) for t in out)
        assert [t.price for t in out] == [150.0, 151.0]
        assert [t.trade_id for t in out] == [7, None]
        assert all(t.side is None for t in out)

    def test_empty(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "list_trades", lambda *a, **k: [])
        assert adapter.fetch_ticks("AAPL") == []


class TestFetchOhlcvEdges:
    @staticmethod
    def _old_bar(ts_ms):
        return {
            "t": ts_ms,
            "o": 1.0,
            "h": 2.0,
            "l": 0.5,
            "c": 1.5,
            "v": 100.0,
            "vw": 1.2,
            "n": 5,
            "otc": None,
        }

    def test_empty_raises(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "aggs", lambda *a, **k: [])
        from datakodo.core.exceptions import DataNotAvailableError

        with pytest.raises(DataNotAvailableError):
            adapter.fetch_ohlcv(
                "AAPL", "1h", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
            )

    def test_include_live_keeps_forming_bar(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        raw = [{"t": now_ms, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 5.0}]
        monkeypatch.setattr(adapter._rest, "aggs", lambda *a, **k: raw)
        from datakodo.core.exceptions import DataNotAvailableError

        start = datetime.now(UTC) - timedelta(hours=1)
        end = datetime.now(UTC)
        with pytest.raises(DataNotAvailableError):
            adapter.fetch_ohlcv("AAPL", "1h", start, end)
        df = adapter.fetch_ohlcv("AAPL", "1h", start, end, include_live=True)
        assert len(df) == 1
        assert not df["is_closed"].iloc[0]

    def test_columns_all_includes_extras(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        monkeypatch.setattr(adapter._rest, "aggs", lambda *a, **k: [self._old_bar(old)])
        df = adapter.fetch_ohlcv(
            "AAPL",
            "1h",
            datetime.now(UTC) - timedelta(days=3),
            datetime.now(UTC) - timedelta(days=1),
            columns="all",
        )
        for col in ("session", "vwap", "trades_count"):
            assert col in df.columns

    def test_explicit_columns(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        monkeypatch.setattr(adapter._rest, "aggs", lambda *a, **k: [self._old_bar(old)])
        df = adapter.fetch_ohlcv(
            "AAPL",
            "1h",
            datetime.now(UTC) - timedelta(days=3),
            datetime.now(UTC) - timedelta(days=1),
            columns=["vwap"],
        )
        assert "vwap" in df.columns
        assert "session" not in df.columns

    def test_invalid_timeframe(self):
        adapter = MassiveAdapter(api_key="test-key")
        from datakodo.core.exceptions import InvalidTimeframeError

        with pytest.raises(InvalidTimeframeError):
            adapter.fetch_ohlcv(
                "AAPL", "9m", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
            )

    def test_output_format_polars(self, monkeypatch):
        import polars as pl

        adapter = MassiveAdapter(api_key="test-key")
        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        monkeypatch.setattr(adapter._rest, "aggs", lambda *a, **k: [self._old_bar(old)])
        out = adapter.fetch_ohlcv(
            "AAPL",
            "1h",
            datetime.now(UTC) - timedelta(days=3),
            datetime.now(UTC) - timedelta(days=1),
            output_format="polars",
        )
        assert isinstance(out, pl.DataFrame)
        assert len(out) == 1

    def test_default_date_range(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        seen = {}

        def _fake_aggs(symbol, timeframe, start, end, adjust=True, limit=5000):
            seen["start"] = start
            seen["end"] = end
            return [self._old_bar(old)]

        monkeypatch.setattr(adapter._rest, "aggs", _fake_aggs)
        df = adapter.fetch_ohlcv("AAPL", "1h")
        assert len(df) == 1
        assert (seen["end"] - seen["start"]).days == 30

    def test_batch_inherits_threadpool(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        raw = [{"t": old, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100.0}]
        monkeypatch.setattr(adapter._rest, "aggs", lambda *a, **k: raw)
        out = adapter.fetch_ohlcv_batch(
            ["AAPL", "MSFT"],
            "1h",
            datetime.now(UTC) - timedelta(days=3),
            datetime.now(UTC) - timedelta(days=1),
        )
        assert set(out) == {"AAPL", "MSFT"}
        assert all(len(frame) == 1 for frame in out.values())


class TestSearchInstrumentsEdges:
    @staticmethod
    def _tickers():
        return [
            {
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "market": "stocks",
                "type": "stock",
                "primary_exchange": "NMS",
                "currency_name": "usd",
            },
            {
                "ticker": "O:AAPL260116C00250000",
                "name": "AAPL Call",
                "market": "options",
                "type": "option",
                "primary_exchange": "CBOE",
                "currency_name": "usd",
            },
            {
                "ticker": "SAP",
                "name": "SAP SE",
                "market": "stocks",
                "type": "stock",
                "primary_exchange": "XETRA",
                "currency_name": "eur",
            },
            {
                "ticker": "",
                "name": "Empty",
                "market": "stocks",
                "type": "stock",
                "primary_exchange": "NMS",
                "currency_name": "usd",
            },
        ]

    def test_limit_breaks(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "list_tickers", lambda *a, **k: self._tickers())
        assert len(adapter.search_instruments("", limit=1)) == 1

    def test_skips_empty_symbol(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "list_tickers", lambda *a, **k: self._tickers())
        symbols = [i.symbol for i in adapter.search_instruments("")]
        assert "" not in symbols and len(symbols) == 3

    def test_skips_map_failures(self, monkeypatch):
        import datakodo.adapters.massive.adapter as _mod

        orig = _mod.map_instrument

        def _flaky(symbol, ticker, market=None):
            if symbol == "SAP":
                raise RuntimeError("boom")
            return orig(symbol, ticker, market=market)

        monkeypatch.setattr(_mod, "map_instrument", _flaky)
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "list_tickers", lambda *a, **k: self._tickers())
        symbols = [i.symbol for i in adapter.search_instruments("")]
        assert "SAP" not in symbols and len(symbols) == 2

    def test_quote_filter(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "list_tickers", lambda *a, **k: self._tickers())
        assert [i.symbol for i in adapter.search_instruments("", quote="EUR")] == ["SAP"]

    def test_exchange_filter(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "list_tickers", lambda *a, **k: self._tickers())
        out = adapter.search_instruments("", exchange="CBOE")
        assert [i.symbol for i in out] == ["O:AAPL260116C00250000"]

    def test_instrument_type_filter(self, monkeypatch):
        adapter = MassiveAdapter(api_key="test-key")
        monkeypatch.setattr(adapter._rest, "list_tickers", lambda *a, **k: self._tickers())
        out = adapter.search_instruments("", instrument_type=InstrumentType.OPTION)
        assert [i.symbol for i in out] == ["O:AAPL260116C00250000"]


# --- mapper edges ---------------------------------------------------------------


class TestMapperScalars:
    def test_to_ms(self):
        from datakodo.adapters.massive.mapper import _to_ms

        assert _to_ms(1700000000000) == 1700000000000
        assert _to_ms(1700000000000000000) == 1700000000000
        assert _to_ms("1700000000000") == 1700000000000

    def test_safe_float(self):
        from datakodo.adapters.massive.mapper import _safe_float

        assert _safe_float(None) is None
        assert _safe_float(3) == 3.0

    def test_session_for(self):
        from datakodo.adapters.massive.mapper import _session_for

        assert _session_for("stocks") == "regular"
        assert _session_for("crypto") is None
        assert _session_for("forex") is None


class TestMapOhlcvEdges:
    def test_empty_raw(self):
        from datakodo.adapters.massive.mapper import map_ohlcv

        df = map_ohlcv([], "1h")
        assert len(df) == 0
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "is_closed",
        ]

    def test_columns_all(self):
        from datakodo.adapters.massive.mapper import map_ohlcv

        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        raw = [
            {
                "t": old,
                "o": 1.0,
                "h": 2.0,
                "l": 0.5,
                "c": 1.5,
                "v": 100.0,
                "vw": 1.2,
                "n": 5,
                "otc": False,
            }
        ]
        df = map_ohlcv(raw, "1h", market="stocks", columns="all")
        assert list(df.columns[:7]) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "is_closed",
        ]
        assert {"session", "vwap", "trades_count", "otc"} <= set(df.columns)
        assert df["session"].iloc[0] == "regular"

    def test_dedupes_pages(self):
        from datakodo.adapters.massive.mapper import map_ohlcv

        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        raw = [
            {"t": old, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.0, "v": 10.0},
            {"t": old, "o": 1.0, "h": 2.0, "l": 0.5, "c": 2.0, "v": 20.0},
        ]
        df = map_ohlcv(raw, "1h")
        assert len(df) == 1
        assert df["close"].iloc[0] == 2.0  # keep last

    def test_is_closed_derivation(self):
        from datakodo.adapters.massive.mapper import map_ohlcv

        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        old_ms = int((datetime.now(UTC) - timedelta(hours=5)).timestamp() * 1000)
        df = map_ohlcv(
            [
                {"t": old_ms, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
                {"t": now_ms, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            ],
            "1h",
        )
        assert list(df["is_closed"]) == [True, False]

    def test_unknown_keys_pass_through(self):
        from datakodo.adapters.massive.mapper import map_ohlcv

        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        df = map_ohlcv(
            [{"t": old, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1, "foo": "bar"}],
            "1h",
            columns="all",
        )
        assert df["foo"].iloc[0] == "bar"

    def test_missing_ohlc_filled(self):
        from datakodo.adapters.massive.mapper import map_ohlcv

        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        df = map_ohlcv([{"t": old}], "1h")
        assert df["open"].isna().all()

    def test_unavailable_extra_raises(self):
        from datakodo.adapters.massive.mapper import map_ohlcv

        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        with pytest.raises(ValueError, match="not available"):
            map_ohlcv(
                [{"t": old, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}], "1h", columns=["spread"]
            )

    def test_crypto_has_no_session(self):
        from datakodo.adapters.massive.mapper import map_ohlcv

        old = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)
        df = map_ohlcv(
            [{"t": old, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
            "1h",
            market="crypto",
            columns="all",
        )
        assert df["session"].iloc[0] is None


class TestMapRestTrades:
    def test_basic_ns_timestamp(self):
        from datakodo.adapters.massive.mapper import map_rest_trades

        out = map_rest_trades(
            [{"price": 150.0, "size": 10.0, "sip_timestamp": 1700000000000000000, "id": 5}]
        )
        assert len(out) == 1
        assert out[0].timestamp == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
        assert (out[0].price, out[0].size, out[0].side, out[0].trade_id) == (
            150.0,
            10.0,
            None,
            5,
        )

    def test_fallbacks_and_skips(self):
        from datakodo.adapters.massive.mapper import map_rest_trades

        rows = [
            {"price": 1.0, "size": 1.0, "participant_timestamp": 1700000000000000000, "id": "x"},
            {"price": 2.0, "size": 2.0, "t": 1700000001000, "id": 9},
            {"size": 1.0, "t": 1},  # no price -> skipped
            {"price": 1.0, "t": 1},  # no size -> skipped
        ]
        out = map_rest_trades(rows)
        assert len(out) == 2
        assert out[0].trade_id is None
        assert out[1].trade_id == 9

    def test_empty(self):
        from datakodo.adapters.massive.mapper import map_rest_trades

        assert map_rest_trades([]) == []


class TestMapInstrumentExtras:
    def test_perpetual_detected(self):
        inst = map_instrument(
            "X:BTCUSD",
            {
                "ticker": "X:BTCUSD",
                "market": "crypto",
                "type": "perpetual",
                "primary_exchange": "CBSE",
                "currency_name": "usd",
            },
        )
        assert inst.instrument_type == InstrumentType.PERPETUAL

    def test_type_refinement_overrides_market(self):
        stock_as_option = map_instrument(
            "X",
            {
                "ticker": "X",
                "market": "stocks",
                "type": "option",
                "primary_exchange": "CBOE",
                "currency_name": "usd",
            },
        )
        assert stock_as_option.instrument_type == InstrumentType.OPTION
        stock_as_future = map_instrument(
            "Y",
            {
                "ticker": "Y",
                "market": "stocks",
                "type": "future",
                "primary_exchange": "CME",
                "currency_name": "usd",
            },
        )
        assert stock_as_future.instrument_type == InstrumentType.FUTURE

    def test_market_hint_fallback(self):
        inst = map_instrument("X:BTCUSD", {"ticker": "X:BTCUSD"}, market="crypto")
        assert inst.asset_class == AssetClass.CRYPTO

    def test_unknown_market_defaults(self):
        inst = map_instrument("ZZZ", {"ticker": "ZZZ", "market": "weird"})
        assert inst.asset_class == AssetClass.EQUITY
        assert inst.instrument_type == InstrumentType.SPOT
        assert inst.currency == "USD"
        assert inst.exchange == "Massive"


# --- WS gaps --------------------------------------------------------------------


class TestMassiveWSGaps:
    def test_subscribe_dedup_and_unsubscribe_noop(self, monkeypatch):
        ws = _make_ws(monkeypatch)

        async def go():
            await ws.connect("stocks")
            await ws.subscribe("T", "AAPL")
            await ws.subscribe("T", "AAPL")
            assert ws._client.subscribed == ["T.AAPL"]
            await ws.unsubscribe("T", "MSFT")  # not subscribed -> noop
            assert ws._client.unsubscribed == []
            await ws.unsubscribe("T", "AAPL")
            assert ws._client.unsubscribed == ["T.AAPL"]
            await ws.disconnect()

        asyncio.run(go())

    def test_stream_trades_alias(self, monkeypatch):
        ws = _make_ws(monkeypatch)

        async def collect():
            out = []
            gen = ws.stream_trades("AAPL")
            try:
                async for trade in gen:
                    out.append(trade)
                    if len(out) == 1:
                        break
            finally:
                await gen.aclose()
            return out

        async def drive():
            task = asyncio.create_task(collect())
            for _ in range(100):
                await asyncio.sleep(0.01)
                if ws._client is not None and ws._client.handler is not None:
                    break
            import json as _json

            await ws._client.handler(
                _json.dumps(
                    {"ev": "T", "sym": "AAPL", "p": 9.0, "s": 1, "t": 1700000000000, "i": 3}
                )
            )
            trades = await asyncio.wait_for(task, 5)
            await ws.disconnect()
            return trades

        trades = asyncio.run(drive())
        assert len(trades) == 1 and trades[0].price == 9.0

    def test_connect_non_auth_error(self, monkeypatch):
        class _BoomClient(_FakeWSClient):
            async def connect(self, handler, *args, **kwargs):
                raise RuntimeError("boom")

        monkeypatch.setattr("datakodo.adapters.massive.ws.WebSocketClient", _BoomClient)
        from datakodo.adapters.massive.ws import MassiveWS

        ws = MassiveWS(api_key="test-key")

        async def go():
            with pytest.raises(DataConnectionError):
                await ws.connect("stocks")

        asyncio.run(go())

    def test_dead_connection_errors(self):
        from massive.websocket import AuthError

        from datakodo.adapters.massive.ws import MassiveWS

        async def _finished(exc=None):
            async def _body():
                if exc is not None:
                    raise exc

            task = asyncio.create_task(_body())
            for _ in range(100):
                if task.done():
                    break
                await asyncio.sleep(0.01)
            return task

        async def go():
            ws = MassiveWS(api_key="test-key")
            ws._task = await _finished(AuthError("bad"))
            assert isinstance(ws._dead_connection_error("AAPL"), AuthenticationError)
            ws._task = await _finished(RuntimeError("gone"))
            err = ws._dead_connection_error("AAPL")
            assert isinstance(err, DataConnectionError) and "gone" in str(err)
            ws._task = await _finished(None)
            assert isinstance(ws._dead_connection_error("AAPL"), DataConnectionError)
            ws._task = None
            assert isinstance(ws._dead_connection_error("AAPL"), DataConnectionError)

        asyncio.run(go())

    def test_disconnect_idempotent(self, monkeypatch):
        ws = _make_ws(monkeypatch)

        async def go():
            await ws.disconnect()  # never connected -> fine
            await ws.connect("stocks")
            await ws.disconnect()
            await ws.disconnect()
            assert not ws.connected

        asyncio.run(go())

    def test_context_manager(self, monkeypatch):
        ws = _make_ws(monkeypatch)

        async def go():
            async with ws as entered:
                assert entered is ws

        asyncio.run(go())
        assert not ws.connected

    def test_on_message_bytes_and_invalid(self, monkeypatch):
        ws = _make_ws(monkeypatch)

        async def go():
            await ws._on_message(b'[{"ev": "T", "p": 1}]')
            assert ws._queue.qsize() == 1
            await ws._on_message("not json")
            await ws._on_message(b"\xff\xfe")
            assert ws._queue.qsize() == 1

        asyncio.run(go())

    def test_recv_timeout_then_disconnect(self, monkeypatch):
        ws = _make_ws(monkeypatch)
        monkeypatch.setattr("datakodo.adapters.massive.ws._RECV_TIMEOUT", 0.05)
        errors = []

        async def consume():
            try:
                async for _ in ws.trade_stream("AAPL"):
                    pass
            except Exception as exc:
                errors.append(exc)

        async def drive():
            task = asyncio.create_task(consume())
            for _ in range(200):
                await asyncio.sleep(0.01)
                if ("T", "AAPL") in ws._subscribed:
                    break
            await asyncio.sleep(0.2)
            await ws.disconnect()
            await asyncio.wait_for(task, 5)

        asyncio.run(drive())
        assert len(errors) == 1
        assert isinstance(errors[0], DataConnectionError)


# --- config + contract ------------------------------------------------------------


class TestMassiveConfig:
    def test_defaults(self):
        from datakodo.adapters.massive.config import MassiveConfig

        cfg = MassiveConfig(api_key="test-key")
        assert cfg.base_url == "https://api.massive.com"
        assert cfg.timeout == 10.0
        assert cfg.rate_limit_rate == 0.1
        assert cfg.rate_limit_burst == 5

    def test_explicit_key_wins(self):
        from datakodo.adapters.massive.config import MassiveConfig

        assert MassiveConfig(api_key="abc").api_key == "abc"


class TestMassiveContract:
    def test_registered_with_client(self):
        from datakodo.client import Client

        assert "massive" in Client.available_providers()
        client = Client("massive", api_key="test-key")
        assert isinstance(client.adapter, MassiveAdapter)

    def test_honest_unsupported_surface(self):
        from datakodo.core.exceptions import NotSupportedError
        from datakodo.core.interfaces import check_capability

        adapter = MassiveAdapter(api_key="test-key")
        with pytest.raises(NotSupportedError):
            check_capability(adapter, "supports_orderbook_snapshot")
        with pytest.raises(NotSupportedError):
            adapter.fetch_orderbook_snapshot("AAPL")
        # Base default is a plain coroutine that raises (not an async generator).
        with pytest.raises(NotSupportedError):
            asyncio.run(adapter.stream_orderbook("AAPL"))

    def test_context_manager_is_noop(self):
        with MassiveAdapter(api_key="test-key"):
            pass


# --- last-mile coverage -----------------------------------------------------------


class TestSearchMatchFuture:
    def test_query_matches_future_underlying(self):
        from datakodo.core.instruments import FutureExtension

        adapter = MassiveAdapter()
        inst = Instrument(
            symbol="GCJ5",
            provider_symbol="GCJ5",
            exchange="CME",
            currency="USD",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.FUTURE,
            future=FutureExtension(underlying="GOLD"),
        )
        assert (
            adapter._search_match(
                inst,
                query="gold",
                asset_class=None,
                instrument_type=None,
                quote=None,
                exchange=None,
            )
            is True
        )
        assert (
            adapter._search_match(
                inst,
                query="zzz",
                asset_class=None,
                instrument_type=None,
                quote=None,
                exchange=None,
            )
            is False
        )


class TestMapOhlcvNoTimestamp:
    def test_missing_timestamp_column(self):
        from datakodo.adapters.massive.mapper import map_ohlcv

        df = map_ohlcv([{"o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0}], "1h")
        assert len(df) == 1
        assert df["timestamp"].isna().all()


class TestResolutionNonStr:
    def test_non_str_unknown_raises(self):
        from datakodo.adapters.massive.rest import massive_resolution

        with pytest.raises(ValueError, match="Unknown timeframe"):
            massive_resolution(123)


class TestLocalLimiterRetry:
    def test_sleeps_then_exhausts_without_http(self, monkeypatch):
        rest, sleeps = _make_rest(monkeypatch, max_retries=1)
        from datakodo.core.exceptions import RetriesExhaustedError

        monkeypatch.setattr(rest._limiter, "consume", lambda weight=1: False)
        monkeypatch.setattr(rest._limiter, "wait_time", lambda weight=1: 0.5)
        rest.client = _FakeHTTP([_FakeResp(200, b'{"results": []}')])
        with pytest.raises(RetriesExhaustedError, match="local rate limit"):
            rest._get("/x")
        assert sleeps == [0.5]  # one backoff, then give up
        assert rest.client.calls == []  # never reached the wire


class TestWSLastMile:
    def test_unmappable_frame_skipped_with_warning(self, monkeypatch, caplog):
        import logging

        ws = _make_ws(monkeypatch)

        async def collect():
            out = []
            gen = ws.trade_stream("AAPL")
            try:
                async for trade in gen:
                    out.append(trade)
                    if len(out) == 1:
                        break
            finally:
                await gen.aclose()
            return out

        async def drive():
            task = asyncio.create_task(collect())
            for _ in range(100):
                await asyncio.sleep(0.01)
                if ws._client is not None and ws._client.handler is not None:
                    break
            import json as _json

            await ws._client.handler(_json.dumps({"ev": "T", "sym": "AAPL", "s": 5}))
            await ws._client.handler(
                _json.dumps(
                    {"ev": "T", "sym": "AAPL", "p": 2.0, "s": 1, "t": 1700000000000, "i": 1}
                )
            )
            trades = await asyncio.wait_for(task, 5)
            await ws.disconnect()
            return trades

        with caplog.at_level(logging.WARNING, logger="datakodo.adapters.massive.ws"):
            trades = asyncio.run(drive())
        assert [t.price for t in trades] == [2.0]
        assert any("Skipping unmappable" in r.message for r in caplog.records)

    def test_unsubscribe_failure_swallowed_on_exit(self, monkeypatch):
        ws = _make_ws(monkeypatch)

        async def go():
            async def consume():
                async for _ in ws.trade_stream("AAPL"):
                    pass

            task = asyncio.create_task(consume())
            for _ in range(200):
                await asyncio.sleep(0.01)
                if ("T", "AAPL") in ws._subscribed:
                    break
            assert ("T", "AAPL") in ws._subscribed
            ws._client = None  # transport died; connection task still alive
            task.cancel()  # finally: unsubscribe raises ConnectionError -> pass
            try:
                await task
            except asyncio.CancelledError:
                pass
            await ws.disconnect()

        asyncio.run(go())

    def test_disconnect_consumes_dead_task(self, monkeypatch):
        ws = _make_ws(monkeypatch)

        async def go():
            await ws.connect("stocks")
            real = ws._task

            async def _boom():
                raise RuntimeError("dead")

            dead = asyncio.create_task(_boom())
            try:
                await dead
            except RuntimeError:
                pass
            ws._task = dead
            await ws.disconnect()  # consumes dead.exception(), no warning
            assert not ws.connected
            real.cancel()
            try:
                await real
            except asyncio.CancelledError:
                pass

        asyncio.run(go())

    def test_close_failure_swallowed(self, monkeypatch):
        class _NastyCloseClient(_FakeWSClient):
            async def close(self):
                raise RuntimeError("close boom")

        monkeypatch.setattr("datakodo.adapters.massive.ws.WebSocketClient", _NastyCloseClient)
        from datakodo.adapters.massive.ws import MassiveWS

        ws = MassiveWS(api_key="test-key")

        async def go():
            await ws.connect("stocks")
            await ws.disconnect()  # close() raises -> logged, not raised
            assert not ws.connected

        asyncio.run(go())
