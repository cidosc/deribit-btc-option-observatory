"""Lossless Deribit envelopes to frozen domain DTO adapters.

This module intentionally performs no valuation or candidate selection.  Invalid
required payloads raise after callers have recorded the raw event.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..domain import (
    DeribitGreeks,
    FieldPresence,
    InstrumentSpec,
    InstrumentState,
    IvValues,
    MarketSnapshot,
    OptionType,
    QualityCode,
    QualityFlag,
    QualityScope,
    QualitySeverity,
    RawField,
    RawMarketEvent,
    SourceMetadata,
    SourceTransport,
    TickSizeStep,
    UnitStatus,
    ValueState,
)


class NormalizationError(ValueError):
    """The raw event remains recordable but cannot produce the requested DTO."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def record_raw_event(
    payload: Any,
    *,
    transport: SourceTransport,
    channel_or_method: str,
    observed_at: datetime | None = None,
    ingested_at: datetime | None = None,
    exchange_timestamp_ms: int | None = None,
    rpc_id: str | int | None = None,
) -> RawMarketEvent:
    """Create a deterministic, lossless raw event suitable for idempotent replay."""
    encoded = canonical_json(payload)
    digest = hashlib.sha256(encoded).hexdigest()
    if exchange_timestamp_ms is None and isinstance(payload, dict):
        try:
            candidate = _result(payload).get("timestamp")
            if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
                exchange_timestamp_ms = candidate
        except NormalizationError:
            pass
    observed = observed_at or utc_now()
    ingested = ingested_at or observed
    source = SourceMetadata(transport, channel_or_method, observed, ingested, exchange_timestamp_ms)
    return RawMarketEvent(
        raw_event_id=f"deribit:{digest}",
        payload_sha256=digest,
        payload=encoded,
        source=source,
        rpc_id=rpc_id,
    )


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("method") == "subscription":
        params = payload.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("data"), dict):
            raise NormalizationError("subscription params.data must be an object")
        return params["data"]
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise NormalizationError("Deribit result must be an object")
    return result


def _decimal(
    value: Any, name: str, *, positive: bool = False, nonnegative: bool = False
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise NormalizationError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise NormalizationError(f"{name} must be numeric") from exc
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise NormalizationError(f"{name} is outside its allowed range")
    return result


def _raw_decimal(mapping: dict[str, Any], key: str) -> RawField[Decimal]:
    if key not in mapping:
        return RawField(FieldPresence.ABSENT)
    if mapping[key] is None:
        return RawField(FieldPresence.PRESENT_NULL)
    try:
        return RawField(FieldPresence.PRESENT_VALUE, _decimal(mapping[key], key))
    except NormalizationError:
        return RawField(FieldPresence.PRESENT_NULL)


def _raw_string(mapping: dict[str, Any], key: str) -> RawField[str]:
    if key not in mapping:
        return RawField(FieldPresence.ABSENT)
    if mapping[key] is None:
        return RawField(FieldPresence.PRESENT_NULL)
    if not isinstance(mapping[key], str):
        return RawField(FieldPresence.PRESENT_NULL)
    return RawField(FieldPresence.PRESENT_VALUE, mapping[key])


def _flag(
    code: QualityCode, message: str, scope: QualityScope = QualityScope.SNAPSHOT
) -> QualityFlag:
    if code.value in {"ZERO_QUOTE_SENTINEL", "DELIVERED_FINAL_STATE", "REPLAY_SOURCE"}:
        severity = QualitySeverity.INFO
    elif code in {
        QualityCode.DISCONNECTED_OR_HEARTBEAT_TIMEOUT,
        QualityCode.STALE_EXCHANGE_TIMESTAMP,
        QualityCode.TIMESTAMP_OUT_OF_ORDER,
        QualityCode.CLOCK_SKEW_EXCEEDED,
        QualityCode.SNAPSHOT_TIME_MISMATCH,
        QualityCode.NON_OPEN_STATE,
        QualityCode.STATE_ACTIVITY_INCONSISTENT,
        QualityCode.BOOK_SEQUENCE_GAP,
        QualityCode.UNKNOWN_SCHEMA_VERSION,
        QualityCode.SCHEMA_FIELD_ABSENT_REQUIRED,
        QualityCode.FIELD_TYPE_MISMATCH_REQUIRED,
        QualityCode.UNVERIFIED_DECISION_UNIT,
        QualityCode.CROSSED_MARKET,
        QualityCode.INVALID_PRICE,
        QualityCode.UNKNOWN_TRADE_SPEC,
    }:
        severity = QualitySeverity.BLOCK_ACTION
    elif code in {
        QualityCode.NO_EXECUTABLE_BID,
        QualityCode.NO_EXECUTABLE_ASK,
        QualityCode.NO_MARK,
        QualityCode.IV_NOT_AVAILABLE,
        QualityCode.INVALID_IV,
        QualityCode.GREEK_UNIT_UNVERIFIED,
        QualityCode.FEE_UNVERIFIED,
        QualityCode.INVALID_QUANTITY,
        QualityCode.ESTIMATED_DELIVERY_NOT_NUMERIC,
    }:
        severity = QualitySeverity.BLOCK_VALUE
    else:
        severity = QualitySeverity.WARNING
    return QualityFlag(code, severity, scope, message)


def _state(raw: Any) -> InstrumentState:
    values = {s.value.lower(): s for s in InstrumentState if s is not InstrumentState.UNKNOWN}
    return (
        values.get(raw, InstrumentState.UNKNOWN)
        if isinstance(raw, str)
        else InstrumentState.UNKNOWN
    )


def normalize_instrument(payload: dict[str, Any], raw_event: RawMarketEvent) -> InstrumentSpec:
    data = _result(payload)
    required = (
        "instrument_name",
        "option_type",
        "state",
        "strike",
        "expiration_timestamp",
        "contract_size",
        "min_trade_amount",
        "tick_size",
    )
    missing = [field for field in required if field not in data]
    if missing:
        raise NormalizationError(f"instrument required fields absent: {', '.join(missing)}")
    option_types = {"call": OptionType.CALL, "put": OptionType.PUT}
    if data["option_type"] not in option_types:
        raise NormalizationError("unsupported option_type")
    steps: list[TickSizeStep] = []
    for step in data.get("tick_size_steps", []) or []:
        if not isinstance(step, dict):
            raise NormalizationError("tick_size_steps item must be object")
        steps.append(
            TickSizeStep(
                _decimal(step.get("above_price"), "above_price"),
                _decimal(step.get("tick_size"), "tick_size", positive=True),
            )
        )
    flags: list[QualityFlag] = []
    state = _state(data["state"])
    active = data.get("is_active")
    if state is InstrumentState.UNKNOWN:
        flags.append(
            _flag(
                QualityCode.UNKNOWN_SCHEMA_VERSION,
                "unknown instrument lifecycle state",
                QualityScope.INSTRUMENT,
            )
        )
    if isinstance(active, bool) and ((state is InstrumentState.OPEN) != active):
        flags.append(
            _flag(
                QualityCode.STATE_ACTIVITY_INCONSISTENT,
                "state and is_active disagree",
                QualityScope.INSTRUMENT,
            )
        )
    return InstrumentSpec(
        instrument_name=str(data["instrument_name"]),
        option_type=option_types[data["option_type"]],
        state=state,
        strike_usd_per_btc=_decimal(data["strike"], "strike", positive=True),
        expiration_timestamp_ms=_integer(data["expiration_timestamp"], "expiration_timestamp"),
        contract_size_btc=_decimal(data["contract_size"], "contract_size", positive=True),
        min_trade_amount_base_btc=_decimal(
            data["min_trade_amount"], "min_trade_amount", positive=True
        ),
        base_tick_size_btc_per_contract=_decimal(data["tick_size"], "tick_size", positive=True),
        instrument_spec_version="deribit-instrument@1",
        raw_event_id=raw_event.raw_event_id,
        instrument_id=data.get("instrument_id")
        if isinstance(data.get("instrument_id"), int)
        else None,
        option_type_raw=data.get("option_type"),
        state_raw=data.get("state"),
        instrument_type_raw=data.get("instrument_type"),
        is_active=active if isinstance(active, bool) else None,
        creation_timestamp_ms=data.get("creation_timestamp")
        if isinstance(data.get("creation_timestamp"), int)
        else None,
        price_index_name=data.get("price_index")
        if isinstance(data.get("price_index"), str)
        else None,
        base_currency=str(data.get("base_currency", "BTC")),
        quote_currency=str(data.get("quote_currency", "BTC")),
        counter_currency=str(data.get("counter_currency", "USD")),
        settlement_currency=str(data.get("settlement_currency", "BTC")),
        tick_size_steps=tuple(steps),
        maker_commission_raw=_optional_decimal(data.get("maker_commission")),
        taker_commission_raw=_optional_decimal(data.get("taker_commission")),
        quality_flags=tuple(flags),
    )


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NormalizationError(f"{name} must be an integer")
    return value


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return _decimal(value, "optional")
    except NormalizationError:
        return None


def _value(
    raw: RawField[Decimal],
    code: str,
    message: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> ValueState[Decimal]:
    if raw.presence is not FieldPresence.PRESENT_VALUE or raw.value is None:
        return ValueState.unavailable(code, message)
    value = raw.value
    if (positive and value <= 0) or (nonnegative and value < 0):
        return ValueState.unavailable(code, message)
    return ValueState.available(value)


def _quote(data: dict[str, Any], side: str, state: InstrumentState, flags: list[QualityFlag]):
    from ..domain import Quote

    raw_price = _raw_decimal(data, f"best_{side}_price")
    raw_amount = _raw_decimal(data, f"best_{side}_amount")
    usable = (
        state is InstrumentState.OPEN
        and raw_price.value is not None
        and raw_price.value > 0
        and raw_amount.value is not None
        and raw_amount.value > 0
    )
    if raw_price.value == 0 or raw_amount.value == 0:
        flags.append(_flag(QualityCode.ZERO_QUOTE_SENTINEL, f"{side} zero quote sentinel"))
    if not usable:
        flags.append(
            _flag(
                QualityCode.NO_EXECUTABLE_BID if side == "bid" else QualityCode.NO_EXECUTABLE_ASK,
                f"{side} is not executable",
                QualityScope.VALUE,
            )
        )
    return Quote(
        _value(
            raw_price, f"NO_EXECUTABLE_{side.upper()}", f"{side} price unavailable", positive=True
        )
        if usable
        else ValueState.unavailable(f"NO_EXECUTABLE_{side.upper()}", f"{side} price unavailable"),
        _value(
            raw_amount, f"NO_EXECUTABLE_{side.upper()}", f"{side} amount unavailable", positive=True
        )
        if usable
        else ValueState.unavailable(f"NO_EXECUTABLE_{side.upper()}", f"{side} amount unavailable"),
        raw_price,
        raw_amount,
    )


def normalize_ticker(
    payload: dict[str, Any], raw_event: RawMarketEvent, spec: InstrumentSpec
) -> MarketSnapshot:
    data = _result(payload)
    required = (
        "timestamp",
        "instrument_name",
        "state",
        "index_price",
        "underlying_price",
        "mark_price",
        "open_interest",
        "best_bid_price",
        "best_bid_amount",
        "best_ask_price",
        "best_ask_amount",
        "bid_iv",
        "ask_iv",
        "mark_iv",
        "interest_rate",
        "underlying_index",
        "stats",
        "greeks",
    )
    missing = [field for field in required if field not in data]
    if missing:
        raise NormalizationError(f"ticker required fields absent: {', '.join(missing)}")
    if data["instrument_name"] != spec.instrument_name:
        raise NormalizationError("ticker instrument does not match supplied specification")
    timestamp = _integer(data["timestamp"], "timestamp")
    state = _state(data["state"])
    flags: list[QualityFlag] = []
    if state is not InstrumentState.OPEN:
        flags.append(_flag(QualityCode.NON_OPEN_STATE, f"ticker state is {data['state']}"))
        if state is InstrumentState.DELIVERED:
            flags.append(_flag(QualityCode.DELIVERED_FINAL_STATE, "delivered terminal ticker"))
    if state is InstrumentState.UNKNOWN:
        flags.append(_flag(QualityCode.UNKNOWN_SCHEMA_VERSION, "unknown ticker state"))
    bid, ask = _quote(data, "bid", state, flags), _quote(data, "ask", state, flags)
    if (
        bid.price_btc_per_contract.value is not None
        and ask.price_btc_per_contract.value is not None
        and bid.price_btc_per_contract.value > ask.price_btc_per_contract.value
    ):
        flags.append(_flag(QualityCode.CROSSED_MARKET, "best bid exceeds best ask"))
    mark_raw = _raw_decimal(data, "mark_price")
    mark = _value(mark_raw, "NO_MARK", "mark price unavailable", nonnegative=True)
    if mark.value is None:
        flags.append(_flag(QualityCode.NO_MARK, "mark price unavailable", QualityScope.VALUE))
    iv = _ivs(data, flags)
    greeks = _greeks(data, spec, raw_event.source.exchange_timestamp_ms or timestamp)
    stats = data["stats"]
    if not isinstance(stats, dict):
        raise NormalizationError("stats must be an object")
    if any(stats.get(x) is None for x in ("high", "low", "price_change")):
        flags.append(
            _flag(QualityCode.EMPTY_STATS_OR_NO_TRADE, "24 hour trade statistics are empty")
        )
    estimated_raw = _raw_decimal(data, "estimated_delivery_price")
    estimated = _value(
        estimated_raw,
        "ESTIMATED_DELIVERY_NOT_NUMERIC",
        "estimated delivery unavailable",
        positive=True,
    )
    if estimated.value is None and "estimated_delivery_price" in data:
        flags.append(
            _flag(
                QualityCode.ESTIMATED_DELIVERY_NOT_NUMERIC,
                "estimated delivery is not a positive number",
                QualityScope.VALUE,
            )
        )
    delivery = _value(
        _raw_decimal(data, "delivery_price"),
        "DELIVERY_UNAVAILABLE",
        "delivery price unavailable",
        positive=True,
    )
    index = _value(
        _raw_decimal(data, "index_price"),
        "INDEX_UNAVAILABLE",
        "index price unavailable",
        positive=True,
    )
    underlying = _value(
        _raw_decimal(data, "underlying_price"),
        "UNDERLYING_UNAVAILABLE",
        "underlying price unavailable",
        positive=True,
    )
    oi = _value(
        _raw_decimal(data, "open_interest"),
        "OPEN_INTEREST_UNAVAILABLE",
        "open interest unavailable",
        nonnegative=True,
    )
    volume = _value(
        _raw_decimal(stats, "volume"), "VOLUME_UNAVAILABLE", "volume unavailable", nonnegative=True
    )
    actionable = state is InstrumentState.OPEN and not any(
        flag.severity is QualitySeverity.BLOCK_ACTION for flag in flags
    )
    snapshot_id = hashlib.sha256(
        f"{raw_event.raw_event_id}|{spec.instrument_spec_version}|{spec.schema_version}".encode()
    ).hexdigest()
    return MarketSnapshot(
        market_snapshot_id=f"snapshot:{snapshot_id}",
        instrument_name=spec.instrument_name,
        raw_event_ids=(raw_event.raw_event_id,),
        source=SourceMetadata(
            raw_event.source.source_transport,
            raw_event.source.source_channel_or_method,
            raw_event.source.observed_at_utc,
            raw_event.source.ingested_at_utc,
            timestamp,
        ),
        instrument_spec_version=spec.instrument_spec_version,
        state=state,
        contract_size_btc=spec.contract_size_btc,
        strike_usd_per_btc=spec.strike_usd_per_btc,
        expiration_timestamp_ms=spec.expiration_timestamp_ms,
        bid=bid,
        ask=ask,
        mark_btc_per_contract=mark,
        last_btc_per_contract=_value(
            _raw_decimal(data, "last_price"),
            "LAST_UNAVAILABLE",
            "last price unavailable",
            nonnegative=True,
        ),
        index_price_usd_per_btc=index,
        underlying_price_usd_per_btc=underlying,
        underlying_index_name=_raw_string(data, "underlying_index"),
        estimated_delivery_price_usd_per_btc=estimated,
        delivery_price_usd_per_btc=delivery,
        iv=iv,
        interest_rate_raw=_raw_decimal(data, "interest_rate"),
        greeks=greeks,
        open_interest_amount_base_btc=oi,
        volume_24h_amount_base_btc=volume,
        quality_flags=tuple(flags),
        actionable=actionable,
        instrument_id=spec.instrument_id,
    )


def _ivs(data: dict[str, Any], flags: list[QualityFlag]) -> IvValues:
    raw = {side: _raw_decimal(data, f"{side}_iv") for side in ("bid", "ask", "mark")}
    values: dict[str, ValueState[Decimal]] = {}
    for side, field in raw.items():
        if field.value is not None and field.value > 0:
            values[side] = ValueState.available(field.value / Decimal("100"))
        else:
            values[side] = ValueState.unavailable(
                "IV_NOT_AVAILABLE", f"{side} IV is absent, zero, or invalid"
            )
            flags.append(
                _flag(QualityCode.IV_NOT_AVAILABLE, f"{side} IV is unavailable", QualityScope.VALUE)
            )
    return IvValues(
        raw["bid"], raw["ask"], raw["mark"], values["bid"], values["ask"], values["mark"]
    )


def _greeks(data: dict[str, Any], spec: InstrumentSpec, timestamp: int) -> DeribitGreeks:
    raw_data = data["greeks"]
    if not isinstance(raw_data, dict):
        raise NormalizationError("greeks must be an object")
    raw = {
        name: _raw_decimal(raw_data, name) for name in ("delta", "gamma", "theta", "vega", "rho")
    }

    def present(name: str) -> ValueState[Decimal]:
        return _value(raw[name], "GREEK_UNAVAILABLE", f"{name} unavailable")

    remaining_days = max(
        Decimal("0"), Decimal(spec.expiration_timestamp_ms - timestamp) / Decimal(86_400_000)
    )
    horizon = ValueState.available(min(Decimal("1"), remaining_days))
    return DeribitGreeks(
        raw["delta"],
        raw["gamma"],
        raw["theta"],
        raw["vega"],
        raw["rho"],
        present("delta"),
        ValueState.unavailable("GREEK_UNIT_UNVERIFIED", "gamma scale is unverified"),
        present("theta"),
        present("vega"),
        present("rho"),
        UnitStatus.PROVISIONAL,
        UnitStatus.UNVERIFIED,
        UnitStatus.VERIFIED,
        UnitStatus.VERIFIED,
        UnitStatus.VERIFIED,
        horizon,
    )
