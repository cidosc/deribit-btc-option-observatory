"""Causal, event-time history replay.

This module intentionally accepts recorded domain DTOs rather than a repository.
The caller must supply catalogue revisions and market snapshots as events.  The
replayer never asks a repository for a "latest" instrument, so a historical
decision cannot see an instrument that was only observed later.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Iterator, Mapping

from btc_option_scanner.domain import Availability, InstrumentSpec
from btc_option_scanner.domain.market import MarketSnapshot

REPLAY_SCHEMA_VERSION = "event-time-replay@1.0.0"


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    if value.astimezone(UTC) != value:
        raise ValueError(f"{name} must be UTC")
    return value


class ReplayEventKind(str, Enum):
    CATALOG = "CATALOG"
    MARKET = "MARKET"


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """One observed fact, ordered by ``observed_at_utc``, sequence, and ID.

    ``sequence`` is required only to break ties at the same observed clock.  A
    catalogue update and a ticker received at the same instant can therefore
    retain their recorded arrival order without using exchange time as a proxy
    for visibility.
    """

    event_id: str
    observed_at_utc: datetime
    sequence: int
    kind: ReplayEventKind
    payload: InstrumentSpec | MarketSnapshot

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        _utc(self.observed_at_utc, "observed_at_utc")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        expected = InstrumentSpec if self.kind is ReplayEventKind.CATALOG else MarketSnapshot
        if not isinstance(self.payload, expected):
            raise TypeError(f"{self.kind.value} replay event requires {expected.__name__}")
        if self.kind is ReplayEventKind.MARKET and (
            self.payload.source.observed_at_utc != self.observed_at_utc
        ):
            raise ValueError("market event observed_at_utc must equal snapshot observed_at_utc")

    @classmethod
    def catalog(
        cls, event_id: str, observed_at_utc: datetime, spec: InstrumentSpec, *, sequence: int = 0
    ) -> "ReplayEvent":
        return cls(event_id, observed_at_utc, sequence, ReplayEventKind.CATALOG, spec)

    @classmethod
    def market(cls, event_id: str, snapshot: MarketSnapshot, *, sequence: int = 0) -> "ReplayEvent":
        return cls(
            event_id, snapshot.source.observed_at_utc, sequence, ReplayEventKind.MARKET, snapshot
        )


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    spec: InstrumentSpec
    observed_at_utc: datetime
    event_id: str


@dataclass(frozen=True, slots=True)
class ReplayState:
    """The only state exposed to a strategy at an event-time clock."""

    as_of_utc: datetime
    catalog: Mapping[str, CatalogRecord]
    markets: Mapping[str, MarketSnapshot]
    market_history: Mapping[str, tuple[MarketSnapshot, ...]]
    processed_event_ids: tuple[str, ...]

    def instrument(self, instrument_name: str) -> InstrumentSpec | None:
        record = self.catalog.get(instrument_name)
        return None if record is None else record.spec

    def market(self, instrument_name: str) -> MarketSnapshot | None:
        """Return the latest snapshot visible at this clock, never a future one."""
        return self.markets.get(instrument_name)

    def rolling_high_usd(
        self, instrument_name: str, *, window: timedelta, as_of_utc: datetime | None = None
    ) -> Decimal | None:
        """Index-price high from snapshots in a strictly causal rolling window."""
        if window <= timedelta(0):
            raise ValueError("window must be positive")
        as_of = self.as_of_utc if as_of_utc is None else _utc(as_of_utc, "as_of_utc")
        if as_of > self.as_of_utc:
            raise ValueError("a replay state cannot query a later clock")
        floor = as_of - window
        values = [
            item.index_price_usd_per_btc.value
            for item in self.market_history.get(instrument_name, ())
            if floor <= item.source.observed_at_utc <= as_of
            and item.index_price_usd_per_btc.availability is Availability.AVAILABLE
            and item.index_price_usd_per_btc.value is not None
        ]
        return max(values) if values else None


class EventTimeReplay:
    """Immutable replay tape with deterministic, observed-time visibility."""

    def __init__(self, events: tuple[ReplayEvent, ...] | list[ReplayEvent]) -> None:
        ids = [event.event_id for event in events]
        if len(ids) != len(set(ids)):
            raise ValueError("replay event IDs must be unique")
        self._events = tuple(
            sorted(
                events, key=lambda event: (event.observed_at_utc, event.sequence, event.event_id)
            )
        )

    @property
    def events(self) -> tuple[ReplayEvent, ...]:
        return self._events

    def states(self) -> Iterator[tuple[ReplayEvent, ReplayState]]:
        catalog: dict[str, CatalogRecord] = {}
        markets: dict[str, MarketSnapshot] = {}
        history: dict[str, list[MarketSnapshot]] = {}
        processed: list[str] = []
        for event in self._events:
            if event.kind is ReplayEventKind.CATALOG:
                spec = event.payload
                assert isinstance(spec, InstrumentSpec)
                catalog[spec.instrument_name] = CatalogRecord(
                    spec, event.observed_at_utc, event.event_id
                )
            else:
                snapshot = event.payload
                assert isinstance(snapshot, MarketSnapshot)
                record = catalog.get(snapshot.instrument_name)
                if record is None or record.observed_at_utc > event.observed_at_utc:
                    raise ValueError(
                        "market snapshot "
                        f"{snapshot.market_snapshot_id} has no visible catalogue record"
                    )
                # A future catalogue revision cannot alter this stored snapshot:
                # its historical record is retained in ``market_history``.
                markets[snapshot.instrument_name] = snapshot
                history.setdefault(snapshot.instrument_name, []).append(snapshot)
            processed.append(event.event_id)
            frozen_history = MappingProxyType(
                {name: tuple(items) for name, items in history.items()}
            )
            yield (
                event,
                ReplayState(
                    event.observed_at_utc,
                    MappingProxyType(dict(catalog)),
                    MappingProxyType(dict(markets)),
                    frozen_history,
                    tuple(processed),
                ),
            )

    def state_at(self, as_of_utc: datetime) -> ReplayState:
        """State after all facts observed at or before ``as_of_utc``."""
        as_of = _utc(as_of_utc, "as_of_utc")
        state = ReplayState(
            as_of, MappingProxyType({}), MappingProxyType({}), MappingProxyType({}), ()
        )
        for event, candidate in self.states():
            if event.observed_at_utc > as_of:
                break
            state = candidate
        return replace(state, as_of_utc=as_of)
