"""Raw-event lineage and canonical market state; no network client lives here."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .enums import InstrumentState, QualitySeverity, UnitStatus
from .primitives import (
    DOMAIN_SCHEMA_VERSION,
    QualityFlag,
    RawField,
    SourceMetadata,
    ValueState,
    require_finite,
    require_nonempty,
)


@dataclass(frozen=True, slots=True)
class RawMarketEvent:
    raw_event_id: str
    source: SourceMetadata
    payload_sha256: str
    payload: bytes | str | None = None
    payload_reference: str | None = None
    rpc_id: str | int | None = None

    def __post_init__(self) -> None:
        require_nonempty(self.raw_event_id, "raw_event_id")
        if len(self.payload_sha256) != 64:
            raise ValueError("payload_sha256 must be a SHA-256 hex digest")
        if (self.payload is None) == (self.payload_reference is None):
            raise ValueError("provide exactly one of payload or payload_reference")
        if self.payload_reference is not None:
            require_nonempty(self.payload_reference, "payload_reference")


@dataclass(frozen=True, slots=True)
class Quote:
    price_btc_per_contract: ValueState[Decimal]
    amount_base_btc: ValueState[Decimal]
    raw_price: RawField[Decimal]
    raw_amount: RawField[Decimal]

    @property
    def quantity_contracts(self) -> ValueState[Decimal]:
        # Conversion is deliberately left to a snapshot that binds contract size.
        return ValueState.not_applicable(
            "CONTRACT_SIZE_REQUIRED", "instrument contract size is required"
        )


@dataclass(frozen=True, slots=True)
class IvValues:
    bid_raw: RawField[Decimal]
    ask_raw: RawField[Decimal]
    mark_raw: RawField[Decimal]
    bid_annual_decimal: ValueState[Decimal]
    ask_annual_decimal: ValueState[Decimal]
    mark_annual_decimal: ValueState[Decimal]


@dataclass(frozen=True, slots=True)
class DeribitGreeks:
    delta_raw: RawField[Decimal]
    gamma_raw: RawField[Decimal]
    theta_raw: RawField[Decimal]
    vega_raw: RawField[Decimal]
    rho_raw: RawField[Decimal]
    delta_standard: ValueState[Decimal]
    gamma_standard_per_usd: ValueState[Decimal]
    theta_usd_per_effective_horizon: ValueState[Decimal]
    vega_usd_per_vol_point: ValueState[Decimal]
    rho_usd_per_rate_point: ValueState[Decimal]
    delta_unit_status: UnitStatus
    gamma_unit_status: UnitStatus
    theta_unit_status: UnitStatus
    vega_unit_status: UnitStatus
    rho_unit_status: UnitStatus
    theta_horizon_days: ValueState[Decimal]

    def __post_init__(self) -> None:
        if self.gamma_unit_status is not UnitStatus.UNVERIFIED:
            raise ValueError("v1 gamma unit status must remain UNVERIFIED")
        if self.gamma_standard_per_usd.availability.value == "AVAILABLE":
            raise ValueError("v1 canonical gamma must remain unavailable")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    market_snapshot_id: str
    instrument_name: str
    raw_event_ids: tuple[str, ...]
    source: SourceMetadata
    instrument_spec_version: str
    state: InstrumentState
    contract_size_btc: Decimal
    strike_usd_per_btc: Decimal
    expiration_timestamp_ms: int
    bid: Quote
    ask: Quote
    mark_btc_per_contract: ValueState[Decimal]
    last_btc_per_contract: ValueState[Decimal]
    index_price_usd_per_btc: ValueState[Decimal]
    underlying_price_usd_per_btc: ValueState[Decimal]
    underlying_index_name: RawField[str]
    estimated_delivery_price_usd_per_btc: ValueState[Decimal]
    delivery_price_usd_per_btc: ValueState[Decimal]
    iv: IvValues
    interest_rate_raw: RawField[Decimal]
    greeks: DeribitGreeks
    open_interest_amount_base_btc: ValueState[Decimal]
    volume_24h_amount_base_btc: ValueState[Decimal]
    quality_flags: tuple[QualityFlag, ...]
    actionable: bool
    instrument_id: int | None = None
    schema_version: str = DOMAIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_nonempty(self.market_snapshot_id, "market_snapshot_id")
        require_nonempty(self.instrument_name, "instrument_name")
        if not self.raw_event_ids:
            raise ValueError("MarketSnapshot requires at least one raw_event_id")
        if require_finite(self.contract_size_btc, "contract_size_btc") != Decimal("1"):
            raise ValueError("v1 only accepts contract_size_btc of 1")
        if require_finite(self.strike_usd_per_btc, "strike_usd_per_btc") <= 0:
            raise ValueError("strike_usd_per_btc must be positive")
        if self.actionable and self.state is not InstrumentState.OPEN:
            raise ValueError("only OPEN market snapshots can be actionable")
        if self.actionable and any(
            flag.severity is QualitySeverity.BLOCK_ACTION for flag in self.quality_flags
        ):
            raise ValueError("a BLOCK_ACTION quality flag prevents an actionable snapshot")
        object.__setattr__(self, "raw_event_ids", tuple(self.raw_event_ids))
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))

    @property
    def best_bid_quantity_contracts(self) -> ValueState[Decimal]:
        return _quantity_from_amount(self.bid.amount_base_btc, self.contract_size_btc)

    @property
    def best_ask_quantity_contracts(self) -> ValueState[Decimal]:
        return _quantity_from_amount(self.ask.amount_base_btc, self.contract_size_btc)


def _quantity_from_amount(
    amount: ValueState[Decimal], contract_size_btc: Decimal
) -> ValueState[Decimal]:
    if amount.value is None:
        assert amount.reason is not None
        return ValueState(amount.availability, reason=amount.reason)
    return ValueState.available(amount.value / contract_size_btc)
