"""Recorded raw event replay with deterministic ordering and replay lineage."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime

from ..domain import RawMarketEvent, SourceMetadata, SourceTransport


class EventReplayer:
    def __init__(self, events: Iterable[RawMarketEvent]) -> None:
        self._events = tuple(
            sorted(
                events,
                key=lambda item: (
                    item.source.exchange_timestamp_ms is None,
                    item.source.exchange_timestamp_ms or 0,
                    item.raw_event_id,
                ),
            )
        )

    def replay(self, handler: Callable[[RawMarketEvent, dict], None]) -> int:
        for event in self._events:
            if not isinstance(event.payload, bytes):
                raise ValueError("in-memory raw payload is required for replay")
            payload = json.loads(event.payload)
            now = datetime.now(UTC)
            replayed = replace(
                event,
                source=SourceMetadata(
                    SourceTransport.REPLAY,
                    event.source.source_channel_or_method,
                    event.source.observed_at_utc,
                    now,
                    event.source.exchange_timestamp_ms,
                ),
            )
            handler(replayed, payload)
        return len(self._events)
