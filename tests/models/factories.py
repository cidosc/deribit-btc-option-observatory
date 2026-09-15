from datetime import UTC, datetime
from decimal import Decimal

from btc_option_scanner.domain import (
    FieldPresence,
    InstrumentState,
    QualityCode,
    QualityFlag,
    QualityScope,
    QualitySeverity,
    RawField,
    SourceMetadata,
    SourceTransport,
    UnitStatus,
    ValueState,
)
from btc_option_scanner.domain.market import DeribitGreeks, IvValues, MarketSnapshot, Quote

AS_OF = datetime(2026, 9, 14, 12, tzinfo=UTC)


def market_snapshot(
    *,
    name: str = "BTC-25DEC26-20000-C",
    ask: Decimal | None = Decimal("0.7475"),
    bid: Decimal | None = Decimal("0.7455"),
    mark: Decimal | None = Decimal("0.7467"),
    delta: Decimal | None = Decimal("0.99815"),
    open_interest: Decimal | None = Decimal("4.9"),
    state: InstrumentState = InstrumentState.OPEN,
    stale: bool = False,
    exchange_timestamp_ms: int = 1789388015506,
) -> MarketSnapshot:
    def side(price: Decimal | None, code: str) -> Quote:
        state_value = (
            ValueState.available(price)
            if price is not None
            else ValueState.unavailable(code, f"{code} in test fixture")
        )
        amount = (
            ValueState.available(Decimal("1"))
            if price is not None
            else ValueState.unavailable(code, "amount absent")
        )
        raw = (
            RawField(FieldPresence.PRESENT_VALUE, price)
            if price is not None
            else RawField(FieldPresence.PRESENT_NULL)
        )
        return Quote(state_value, amount, raw, raw)

    unavailable_gamma = ValueState.unavailable("GREEK_UNIT_UNVERIFIED", "gamma is unverified")
    raw = RawField(FieldPresence.PRESENT_VALUE, Decimal("0"))
    delta_state = (
        ValueState.available(delta)
        if delta is not None
        else ValueState.unavailable("DERIBIT_DELTA_UNAVAILABLE", "delta absent")
    )
    greeks = DeribitGreeks(
        raw,
        raw,
        raw,
        raw,
        raw,
        delta_state,
        unavailable_gamma,
        ValueState.available(Decimal("-1.18939")),
        ValueState.available(Decimal("2.46398")),
        ValueState.available(Decimal("55.31467")),
        UnitStatus.PROVISIONAL,
        UnitStatus.UNVERIFIED,
        UnitStatus.VERIFIED,
        UnitStatus.VERIFIED,
        UnitStatus.VERIFIED,
        ValueState.available(Decimal("1")),
    )
    source = SourceMetadata(
        SourceTransport.REPLAY, "test", AS_OF, AS_OF, exchange_timestamp_ms
    )
    flags = []
    if stale:
        flags.append(
            QualityFlag(
                QualityCode.STALE_EXCHANGE_TIMESTAMP,
                QualitySeverity.BLOCK_ACTION,
                QualityScope.SNAPSHOT,
                "stale test snapshot",
            )
        )
    mark_state = (
        ValueState.available(mark)
        if mark is not None
        else ValueState.unavailable("NO_MARK", "mark absent")
    )
    oi = (
        ValueState.available(open_interest)
        if open_interest is not None
        else ValueState.unavailable("OPEN_INTEREST_UNAVAILABLE", "oi absent")
    )
    return MarketSnapshot(
        market_snapshot_id=f"mkt-{name}",
        instrument_name=name,
        raw_event_ids=(f"raw-{name}",),
        source=source,
        instrument_spec_version="test-spec@1",
        state=state,
        contract_size_btc=Decimal("1"),
        strike_usd_per_btc=Decimal("20000"),
        expiration_timestamp_ms=1798185600000,
        bid=side(bid, "NO_EXECUTABLE_BID"),
        ask=side(ask, "NO_EXECUTABLE_ASK"),
        mark_btc_per_contract=mark_state,
        last_btc_per_contract=mark_state,
        index_price_usd_per_btc=ValueState.available(Decimal("77812.18")),
        underlying_price_usd_per_btc=ValueState.available(Decimal("78869.27")),
        underlying_index_name=RawField(FieldPresence.PRESENT_VALUE, "BTC-25DEC26"),
        estimated_delivery_price_usd_per_btc=ValueState.available(Decimal("77812.18")),
        delivery_price_usd_per_btc=ValueState.unavailable("NOT_DELIVERED", "open"),
        iv=IvValues(
            raw,
            RawField(FieldPresence.PRESENT_VALUE, Decimal("1.1327")),
            RawField(FieldPresence.PRESENT_VALUE, Decimal("0.983")),
            ValueState.unavailable("IV_NOT_AVAILABLE", "bid iv missing"),
            ValueState.available(Decimal("1.1327")),
            ValueState.available(Decimal("0.983")),
        ),
        interest_rate_raw=RawField(FieldPresence.PRESENT_VALUE, Decimal("0")),
        greeks=greeks,
        open_interest_amount_base_btc=oi,
        volume_24h_amount_base_btc=ValueState.available(Decimal("0")),
        quality_flags=tuple(flags),
        actionable=not stale and state is InstrumentState.OPEN,
    )
