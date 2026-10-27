"""Instrument model — minimal base + typed asset-class extensions."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from datakodo.core.calendar import SessionProfile
from datakodo.core.enums import (
    AssetClass,
    InstrumentType,
    PriceAdjustment,
    RollRule,
    VolumeType,
)


class InstrumentCapabilities(BaseModel):
    """Data surfaces the provider can supply for this instrument.

    Mirrors the adapter-level capability flags at instrument granularity, so a
    caller can tell what a descriptor supports before fetching. Defaults are
    conservative: only OHLCV is assumed.
    """

    ohlcv: bool = True
    ticks: bool = False
    orderbook_snapshot: bool = False
    streaming_ticks: bool = False
    streaming_orderbook: bool = False
    fundamentals: bool = False
    real_volume: bool = False
    adjusted_prices: bool = False


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
    roll_rule: RollRule = RollRule.NONE
    first_notice_date: str = ""


class OptionExtension(BaseModel):
    strike: float = 0.0
    expiry: str = ""
    right: str = ""  # "call" or "put"
    underlying: str = ""
    contract_size: float = 1.0


class BondExtension(BaseModel):
    coupon: float = 0.0
    maturity: str = ""
    face_value: float = 0.0
    issuer: str = ""
    credit_rating: str = ""


class MetalExtension(BaseModel):
    unit: str = ""  # "oz" or "gram"


class CryptoPerpetualExtension(BaseModel):
    funding_interval: int = 8
    funding_rate: float = 0.0
    contract_size: float = 1.0


class Instrument(BaseModel):
    """Structured instrument descriptor (never a raw string).

    Base fields apply to every instrument. Asset-class-specific fields
    live on typed extensions so the base stays stable forever. The metadata
    block describes what the canonical ``volume`` column counts, how prices
    are quoted, and which session/calendar governs the instrument (design doc
    sec 4, 9, 18).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    asset_class: AssetClass
    instrument_type: InstrumentType
    exchange: str
    currency: str
    provider_symbol: str = ""

    volume_type: VolumeType = VolumeType.NONE
    """What the canonical OHLCV ``volume`` column counts for this provider."""
    contract_multiplier: float = 1.0
    """Units of the underlying per contract (1.0 for spot instruments)."""
    point_value: float = 0.0
    """Currency value of a one-point price move for one contract."""
    tick_size: float = 0.0
    """Minimum price increment, or 0.0 when the provider does not report it."""
    price_precision: int | None = None
    """Decimal places the provider quotes prices at, or ``None`` if unknown."""
    session_profile: SessionProfile | None = None
    """Session profile governing bar boundaries, or ``None`` for 24/7."""
    calendar: str = ""
    """Canonical calendar identifier (``FX_NY17``, ``CME_GLOBEX``, ``XNYS``)."""
    data_tier: str = ""
    """Provider data tier the current client is entitled to (free/iex/sip/...)."""
    price_adjustment: PriceAdjustment = PriceAdjustment.UNKNOWN
    """How the provider's prices relate to raw traded prices."""
    is_cfd: bool = False
    """True for contracts for difference (derived from ``instrument_type``)."""
    allow_negative_prices: bool = False
    """Opt-in for instruments that can settle at or below zero."""
    capabilities: InstrumentCapabilities = Field(default_factory=InstrumentCapabilities)

    equity: EquityExtension | None = None
    forex: ForexExtension | None = None
    future: FutureExtension | None = None
    option: OptionExtension | None = None
    bond: BondExtension | None = None
    metal: MetalExtension | None = None
    crypto_perpetual: CryptoPerpetualExtension | None = None

    @model_validator(mode="after")
    def _derive_is_cfd(self) -> "Instrument":
        """Keep ``is_cfd`` consistent with the settlement structure."""
        if self.instrument_type is InstrumentType.CFD:
            self.is_cfd = True
        return self
