"""Instrument model tests."""

import pytest
from pydantic import ValidationError

from datakodo.core.calendar import FX_NY17
from datakodo.core.enums import AssetClass, InstrumentType, PriceAdjustment, RollRule, VolumeType
from datakodo.core.instruments import (
    CryptoPerpetualExtension,
    EquityExtension,
    ForexExtension,
    FutureExtension,
    Instrument,
    InstrumentCapabilities,
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
            asset_class=AssetClass.INDEX,
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


class TestInstrumentMetadata:
    @staticmethod
    def _symbol(**overrides) -> Instrument:
        fields = {
            "symbol": "EURUSD",
            "asset_class": AssetClass.FOREX,
            "instrument_type": InstrumentType.SPOT,
            "exchange": "OTC",
            "currency": "USD",
        }
        fields.update(overrides)
        return Instrument(**fields)

    def test_metadata_defaults_are_conservative(self):
        inst = self._symbol()
        assert inst.volume_type is VolumeType.NONE
        assert inst.contract_multiplier == 1.0
        assert inst.point_value == 0.0
        assert inst.tick_size == 0.0
        assert inst.price_precision is None
        assert inst.session_profile is None
        assert inst.calendar == ""
        assert inst.data_tier == ""
        assert inst.price_adjustment is PriceAdjustment.UNKNOWN
        assert inst.is_cfd is False
        assert inst.allow_negative_prices is False
        assert inst.capabilities.ohlcv is True
        assert inst.capabilities.ticks is False

    def test_metadata_round_trips(self):
        capabilities = InstrumentCapabilities(ticks=True, fundamentals=True)
        inst = self._symbol(
            volume_type=VolumeType.TICK,
            contract_multiplier=100_000.0,
            point_value=10.0,
            tick_size=0.00001,
            price_precision=5,
            session_profile=FX_NY17,
            calendar="FX_NY17",
            data_tier="broker",
            price_adjustment=PriceAdjustment.RAW,
            allow_negative_prices=True,
            capabilities=capabilities,
        )
        assert inst.volume_type is VolumeType.TICK
        assert inst.contract_multiplier == 100_000.0
        assert inst.point_value == 10.0
        assert inst.tick_size == 0.00001
        assert inst.price_precision == 5
        assert inst.session_profile is FX_NY17
        assert inst.calendar == "FX_NY17"
        assert inst.data_tier == "broker"
        assert inst.price_adjustment is PriceAdjustment.RAW
        assert inst.allow_negative_prices is True
        assert inst.capabilities.ticks is True

    def test_cfd_flag_is_derived_from_instrument_type(self):
        inst = self._symbol(instrument_type=InstrumentType.CFD)
        assert inst.is_cfd is True

    def test_future_extension_roll_metadata(self):
        ext = FutureExtension()
        assert ext.roll_rule is RollRule.NONE
        assert ext.first_notice_date == ""
