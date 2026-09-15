"""Small, unit-explicit values shared by all domain boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Generic, TypeAlias, TypeVar

from .enums import (
    BLOCK_ACTION_CODES,
    BLOCK_VALUE_CODES,
    Availability,
    FieldPresence,
    QualityCode,
    QualityScope,
    QualitySeverity,
    SourceTransport,
)

DOMAIN_SCHEMA_VERSION = "domain-schema-v1"
CORE_METRIC_VERSION = "core-metrics@1.0.0"

# These aliases deliberately retain Decimal at runtime. The explicit names make units visible
# to type checkers and in public annotations; canonical DTOs additionally encode the unit in
# every field name.
Btc: TypeAlias = Decimal
Usd: TypeAlias = Decimal
BtcPerContract: TypeAlias = Decimal
UsdPerBtc: TypeAlias = Decimal
BaseBtcAmount: TypeAlias = Decimal
QuantityContracts: TypeAlias = Decimal
IvAnnualDecimal: TypeAlias = Decimal
UtcTimestamp: TypeAlias = datetime
ExchangeTimestampMs: TypeAlias = int

T = TypeVar("T")


def require_finite(value: Decimal, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal, not {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


def require_nonempty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be an aware UTC datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RawField(Generic[T]):
    """Preserves absent/null/value distinctions at the raw adapter boundary."""

    presence: FieldPresence
    value: T | None = None

    def __post_init__(self) -> None:
        if self.presence is FieldPresence.PRESENT_VALUE and self.value is None:
            raise ValueError("PRESENT_VALUE requires a value")
        if self.presence is not FieldPresence.PRESENT_VALUE and self.value is not None:
            raise ValueError("only PRESENT_VALUE may carry a value")


@dataclass(frozen=True, slots=True)
class UnavailableReason:
    code: str
    message: str

    def __post_init__(self) -> None:
        require_nonempty(self.code, "code")
        require_nonempty(self.message, "message")


@dataclass(frozen=True, slots=True)
class ValueState(Generic[T]):
    """A typed available, unavailable, or not-applicable canonical value."""

    availability: Availability
    value: T | None = None
    reason: UnavailableReason | None = None

    def __post_init__(self) -> None:
        if self.availability is Availability.AVAILABLE:
            if self.value is None or self.reason is not None:
                raise ValueError("AVAILABLE requires value and no reason")
        elif self.value is not None or self.reason is None:
            raise ValueError("unavailable/not-applicable values require a reason and no value")

    @classmethod
    def available(cls, value: T) -> "ValueState[T]":
        return cls(Availability.AVAILABLE, value=value)

    @classmethod
    def unavailable(cls, code: str, message: str) -> "ValueState[T]":
        return cls(Availability.UNAVAILABLE, reason=UnavailableReason(code, message))

    @classmethod
    def not_applicable(cls, code: str, message: str) -> "ValueState[T]":
        return cls(Availability.NOT_APPLICABLE, reason=UnavailableReason(code, message))


@dataclass(frozen=True, slots=True)
class QualityFlag:
    code: QualityCode
    severity: QualitySeverity
    scope: QualityScope
    message: str

    def __post_init__(self) -> None:
        require_nonempty(self.message, "message")
        expected = (
            QualitySeverity.BLOCK_ACTION
            if self.code in BLOCK_ACTION_CODES
            else QualitySeverity.BLOCK_VALUE
            if self.code in BLOCK_VALUE_CODES
            else QualitySeverity.INFO
            if self.code
            in {
                QualityCode.ZERO_QUOTE_SENTINEL,
                QualityCode.DELIVERED_FINAL_STATE,
                QualityCode.REPLAY_SOURCE,
            }
            else QualitySeverity.WARNING
        )
        if self.severity is not expected:
            raise ValueError(f"{self.code.value} must have severity {expected.value}")


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Transport lineage and the three non-interchangeable clocks."""

    source_transport: SourceTransport
    source_channel_or_method: str
    observed_at_utc: datetime
    ingested_at_utc: datetime
    exchange_timestamp_ms: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_transport, SourceTransport):
            raise TypeError("source_transport must be SourceTransport")
        require_nonempty(self.source_channel_or_method, "source_channel_or_method")
        object.__setattr__(
            self, "observed_at_utc", require_utc(self.observed_at_utc, "observed_at_utc")
        )
        object.__setattr__(
            self, "ingested_at_utc", require_utc(self.ingested_at_utc, "ingested_at_utc")
        )
        if self.ingested_at_utc < self.observed_at_utc:
            raise ValueError("ingested_at_utc cannot precede observed_at_utc")
        if self.exchange_timestamp_ms is not None and self.exchange_timestamp_ms < 0:
            raise ValueError("exchange_timestamp_ms must be non-negative")
