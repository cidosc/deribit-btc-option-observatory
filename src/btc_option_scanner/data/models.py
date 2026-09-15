"""Storage rows deliberately separate from domain DTOs (an ORM-ready boundary)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RawEventRow:
    raw_event_id: str
    payload_sha256: str
    source_transport: str
    source_channel_or_method: str
    observed_at_utc: datetime
    ingested_at_utc: datetime
    exchange_timestamp_ms: int | None
    payload: bytes | None
    payload_reference: str | None
    rpc_id: str | int | None


@dataclass(frozen=True, slots=True)
class InstrumentRow:
    instrument_name: str
    instrument_spec_version: str
    raw_event_id: str
    state: str
    observed_at_utc: datetime
    canonical_json: str


@dataclass(frozen=True, slots=True)
class MarketSnapshotRow:
    market_snapshot_id: str
    instrument_name: str
    raw_event_id: str
    exchange_timestamp_ms: int | None
    observed_at_utc: datetime
    ingested_at_utc: datetime
    state: str
    actionable: bool
    canonical_json: str


@dataclass(frozen=True, slots=True)
class DerivedEntityRow:
    entity_id: str
    entity_type: str
    parent_ids: tuple[str, ...]
    as_of_utc: datetime | None
    schema_version: str
    metric_version: str | None
    config_version: str | None
    canonical_json: str
