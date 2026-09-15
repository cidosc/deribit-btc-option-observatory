"""Static and slow-moving option specification DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .enums import InstrumentState, OptionType
from .primitives import DOMAIN_SCHEMA_VERSION, QualityFlag, require_finite, require_nonempty


@dataclass(frozen=True, slots=True)
class TickSizeStep:
    above_price_btc_per_contract: Decimal
    tick_size_btc_per_contract: Decimal

    def __post_init__(self) -> None:
        require_finite(self.above_price_btc_per_contract, "above_price_btc_per_contract")
        if require_finite(self.tick_size_btc_per_contract, "tick_size_btc_per_contract") <= 0:
            raise ValueError("tick_size_btc_per_contract must be positive")


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    instrument_name: str
    option_type: OptionType
    state: InstrumentState
    strike_usd_per_btc: Decimal
    expiration_timestamp_ms: int
    contract_size_btc: Decimal
    min_trade_amount_base_btc: Decimal
    base_tick_size_btc_per_contract: Decimal
    instrument_spec_version: str
    raw_event_id: str
    instrument_id: int | None = None
    option_type_raw: str | None = None
    state_raw: str | None = None
    instrument_type_raw: str | None = None
    is_active: bool | None = None
    creation_timestamp_ms: int | None = None
    price_index_name: str | None = None
    base_currency: str = "BTC"
    quote_currency: str = "BTC"
    counter_currency: str = "USD"
    settlement_currency: str = "BTC"
    tick_size_steps: tuple[TickSizeStep, ...] = ()
    maker_commission_raw: Decimal | None = None
    taker_commission_raw: Decimal | None = None
    quality_flags: tuple[QualityFlag, ...] = ()
    schema_version: str = DOMAIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_nonempty(self.instrument_name, "instrument_name")
        require_nonempty(self.instrument_spec_version, "instrument_spec_version")
        require_nonempty(self.raw_event_id, "raw_event_id")
        if require_finite(self.strike_usd_per_btc, "strike_usd_per_btc") <= 0:
            raise ValueError("strike_usd_per_btc must be positive")
        if self.expiration_timestamp_ms < 0:
            raise ValueError("expiration_timestamp_ms must be non-negative")
        if require_finite(self.contract_size_btc, "contract_size_btc") != Decimal("1"):
            raise ValueError("v1 only accepts contract_size_btc of 1")
        if require_finite(self.min_trade_amount_base_btc, "min_trade_amount_base_btc") <= 0:
            raise ValueError("min_trade_amount_base_btc must be positive")
        if (
            require_finite(self.base_tick_size_btc_per_contract, "base_tick_size_btc_per_contract")
            <= 0
        ):
            raise ValueError("base_tick_size_btc_per_contract must be positive")
        if self.base_currency != "BTC" or self.settlement_currency != "BTC":
            raise ValueError("v1 supports BTC base and settlement only")
        object.__setattr__(self, "tick_size_steps", tuple(self.tick_size_steps))
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))

    @property
    def min_trade_quantity_contracts(self) -> Decimal:
        return self.min_trade_amount_base_btc / self.contract_size_btc
