"""Instrument model — minimal base + typed asset-class extensions."""

from pydantic import BaseModel

from datakodo.core.enums import AssetClass, InstrumentType


class EquityExtension(BaseModel):
    sector: str = ""
    exchange_mic: str = ""
    shares_outstanding: int = 0


class ForexExtension(BaseModel):
    pip_size: float = 0.0001
    lot_size: int = 100_000
    margin_rate: float = 0.0


class FutureExtension(BaseModel):
    expiry: str = ""
    contract_size: float = 0.0
    tick_size: float = 0.0
    multiplier: float = 1.0
    underlying: str = ""


class CryptoPerpetualExtension(BaseModel):
    funding_interval: int = 8
    funding_rate: float = 0.0
    contract_size: float = 1.0


class Instrument(BaseModel):
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

    equity: EquityExtension | None = None
    forex: ForexExtension | None = None
    future: FutureExtension | None = None
    crypto_perpetual: CryptoPerpetualExtension | None = None
