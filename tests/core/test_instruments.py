"""Instrument model tests."""

import pytest
from pydantic import ValidationError

from datakodo.core.enums import AssetClass, InstrumentType
from datakodo.core.instruments import (
    CryptoPerpetualExtension,
    EquityExtension,
    ForexExtension,
    FutureExtension,
    Instrument,
)


class TestInstrument:
    def test_minimal_instrument(self):
        inst = Instrument(
            symbol="AAPL",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
            exchange="NASDAQ",
            currency="USD",
        )
        assert inst.symbol == "AAPL"
        assert inst.asset_class == AssetClass.EQUITY
        assert inst.provider_symbol == ""
        assert inst.equity is None

    def test_instrument_with_equity_extension(self):
        ext = EquityExtension(
            sector="Technology", exchange_mic="XNAS", shares_outstanding=15_000_000_000
        )
        inst = Instrument(
            symbol="AAPL",
            asset_class=AssetClass.EQUITY,
            instrument_type=InstrumentType.SPOT,
            exchange="NASDAQ",
            currency="USD",
            equity=ext,
        )
        assert inst.equity is not None
        assert inst.equity.sector == "Technology"
        assert inst.equity.shares_outstanding == 15_000_000_000

    def test_instrument_with_forex_extension(self):
        ext = ForexExtension(pip_size=0.0001, lot_size=100_000)
        inst = Instrument(
            symbol="EURUSD",
            asset_class=AssetClass.FOREX,
            instrument_type=InstrumentType.SPOT,
            exchange="OTC",
            currency="USD",
            forex=ext,
        )
        assert inst.forex is not None
        assert inst.forex.pip_size == 0.0001

    def test_instrument_with_future_extension(self):
        ext = FutureExtension(expiry="2024-12-20", contract_size=100.0, tick_size=0.25)
        inst = Instrument(
            symbol="ESZ4",
            asset_class=AssetClass.FUTURE,
            instrument_type=InstrumentType.FUTURE,
            exchange="CME",
            currency="USD",
            future=ext,
        )
        assert inst.future is not None
        assert inst.future.tick_size == 0.25

    def test_instrument_with_crypto_perpetual_extension(self):
        ext = CryptoPerpetualExtension(funding_interval=8, funding_rate=0.0001)
        inst = Instrument(
            symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            instrument_type=InstrumentType.PERPETUAL,
            exchange="Binance",
            currency="USDT",
            crypto_perpetual=ext,
        )
        assert inst.crypto_perpetual is not None
        assert inst.crypto_perpetual.funding_rate == 0.0001

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            Instrument(symbol="AAPL")

    def test_default_extension_values(self):
        ext = ForexExtension()
        assert ext.pip_size == 0.0001
        assert ext.lot_size == 100_000
        assert ext.margin_rate == 0.0
