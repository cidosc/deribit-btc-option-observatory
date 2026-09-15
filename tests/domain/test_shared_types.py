from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from btc_option_scanner.domain import (
    Availability,
    FieldPresence,
    InstrumentSpec,
    InstrumentState,
    OptionType,
    QualityCode,
    QualityFlag,
    QualityScope,
    QualitySeverity,
    RawField,
    RawMarketEvent,
    SourceMetadata,
    SourceTransport,
    UnitStatus,
    ValueState,
)
from btc_option_scanner.domain.market import DeribitGreeks


def test_raw_field_preserves_absent_null_and_value() -> None:
    assert RawField(FieldPresence.ABSENT).value is None
    assert RawField(FieldPresence.PRESENT_NULL).value is None
    assert RawField(FieldPresence.PRESENT_VALUE, Decimal("0")).value == Decimal("0")
    with pytest.raises(ValueError):
        RawField(FieldPresence.PRESENT_VALUE)


def test_available_value_cannot_hide_missing_value_as_zero() -> None:
    unavailable = ValueState.unavailable("NO_EXECUTABLE_ASK", "no positive open ask")
    assert unavailable.availability is Availability.UNAVAILABLE
    assert unavailable.value is None
    with pytest.raises(ValueError):
        ValueState(Availability.AVAILABLE, value=None)


def test_source_metadata_requires_distinct_utc_clocks() -> None:
    observed = datetime(2026, 9, 14, tzinfo=UTC)
    metadata = SourceMetadata(
        SourceTransport.WEBSOCKET_SUBSCRIPTION,
        "ticker.BTC",
        observed,
        observed + timedelta(seconds=1),
        1,
    )
    assert metadata.exchange_timestamp_ms == 1
    with pytest.raises(ValueError, match="UTC"):
        SourceMetadata(SourceTransport.REPLAY, "fixture", datetime(2026, 9, 14), observed)


def test_quality_flag_severity_is_frozen_by_contract() -> None:
    flag = QualityFlag(
        QualityCode.NO_EXECUTABLE_ASK, QualitySeverity.BLOCK_VALUE, QualityScope.VALUE, "ask absent"
    )
    assert flag.code is QualityCode.NO_EXECUTABLE_ASK
    with pytest.raises(ValueError):
        QualityFlag(
            QualityCode.NO_EXECUTABLE_ASK, QualitySeverity.WARNING, QualityScope.VALUE, "wrong"
        )


def test_instrument_spec_rejects_non_v1_contract_size() -> None:
    base = dict(
        instrument_name="BTC-25DEC26-20000-C",
        option_type=OptionType.CALL,
        state=InstrumentState.OPEN,
        strike_usd_per_btc=Decimal("20000"),
        expiration_timestamp_ms=1798156800000,
        min_trade_amount_base_btc=Decimal("0.1"),
        base_tick_size_btc_per_contract=Decimal("0.0001"),
        instrument_spec_version="deribit-instrument@1.0.0",
        raw_event_id="raw-1",
    )
    spec = InstrumentSpec(contract_size_btc=Decimal("1"), **base)
    assert spec.min_trade_quantity_contracts == Decimal("0.1")
    with pytest.raises(ValueError, match="contract_size_btc"):
        InstrumentSpec(contract_size_btc=Decimal("0.1"), **base)


def test_gamma_normalized_value_remains_unavailable() -> None:
    raw = RawField(FieldPresence.PRESENT_VALUE, Decimal("0"))
    unavailable = ValueState.unavailable("GREEK_UNIT_UNVERIFIED", "gamma scale is not verified")
    greeks = DeribitGreeks(
        raw,
        raw,
        raw,
        raw,
        raw,
        ValueState.available(Decimal("0.8")),
        unavailable,
        ValueState.available(Decimal("-1")),
        ValueState.available(Decimal("2")),
        ValueState.available(Decimal("3")),
        UnitStatus.PROVISIONAL,
        UnitStatus.UNVERIFIED,
        UnitStatus.VERIFIED,
        UnitStatus.VERIFIED,
        UnitStatus.VERIFIED,
        ValueState.available(Decimal("1")),
    )
    assert greeks.gamma_standard_per_usd.availability is Availability.UNAVAILABLE


def test_raw_event_requires_lossless_payload_or_immutable_reference() -> None:
    source = SourceMetadata(
        SourceTransport.REPLAY,
        "fixture",
        datetime(2026, 9, 14, tzinfo=UTC),
        datetime(2026, 9, 14, tzinfo=UTC),
    )
    event = RawMarketEvent("raw-1", source, "a" * 64, payload=b'{"jsonrpc":"2.0"}')
    assert event.payload_reference is None
    with pytest.raises(ValueError, match="exactly one"):
        RawMarketEvent("raw-2", source, "a" * 64)
