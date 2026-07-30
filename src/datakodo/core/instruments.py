"""Instrument model — minimal base + typed asset-class extensions."""

from dataclasses import dataclass, field

from datakodo.core.enums import AssetClass, InstrumentType


@dataclass
class Instrument:
    """Structured instrument descriptor (never a raw string).

    Base fields apply to every instrument. Asset-class-specific fields
    live on typed extensions so the base stays stable forever.
    """

    symbol: str
    asset_class: AssetClass
    instrument_type: InstrumentType
    exchange: str
    currency: str
    provider_symbol: str = ""

    # Extension blocks — None when not applicable for this asset class.
    equity: "EquityExtension | None" = None
    forex: "ForexExtension | None" = None
    future: "FutureExtension | None" = None
    crypto_perpetual: "CryptoPerpetualExtension | None" = None


@dataclass
class EquityExtension:
    sector: str = ""
    exchange_mic: str = ""
    shares_outstanding: int = 0


@dataclass
class ForexExtension:
    pip_size: float = 0.0001
    lot_size: int = 100_000
    margin_rate: float = 0.0


@dataclass
class FutureExtension:
    expiry: str = ""
    contract_size: float = 0.0
    tick_size: float = 0.0
    multiplier: float = 1.0
    underlying: str = ""


@dataclass
class CryptoPerpetualExtension:
    funding_interval: int = 8
    funding_rate: float = 0.0
    contract_size: float = 1.0
