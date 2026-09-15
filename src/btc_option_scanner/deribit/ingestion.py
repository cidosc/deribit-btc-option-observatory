"""Transactional public-message ingestion: raw evidence first, then canonical rows."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from ..data import SqliteRepository
from ..domain import (
    QualityCode,
    QualityFlag,
    QualityScope,
    QualitySeverity,
    RawMarketEvent,
    SourceTransport,
    SystemEvent,
)
from .lifecycle import apply_lifecycle_event
from .parser import NormalizationError, normalize_instrument, normalize_ticker, record_raw_event


class TickerObservation:
    """One normalized ticker held in memory before any persistence decision."""

    __slots__ = ("raw_event", "snapshot")

    def __init__(self, raw_event: RawMarketEvent, snapshot: Any) -> None:
        self.raw_event = raw_event
        self.snapshot = snapshot


class PublicDataSpine:
    """Coordinates raw archival, catalog state, normalization, and stale/order gates."""

    def __init__(self, repository: SqliteRepository, *, stale_after_seconds: float) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.repository = repository
        self.stale_after_seconds = stale_after_seconds
        self.specs: dict[str, Any] = {}
        self._latest_timestamp: dict[str, int] = {}

    def ingest_instrument(
        self,
        envelope: dict[str, Any],
        *,
        transport: SourceTransport,
        channel: str,
        observed_at: datetime | None = None,
    ):
        raw = record_raw_event(
            envelope, transport=transport, channel_or_method=channel, observed_at=observed_at
        )
        self.repository.save_raw_event(raw)
        try:
            spec = normalize_instrument(envelope, raw)
        except NormalizationError as exc:
            self._failure(
                raw.raw_event_id, "INSTRUMENT_NORMALIZATION_FAILED", str(exc), observed_at
            )
            raise
        self.repository.save_instrument(spec)
        self.specs[spec.instrument_name] = spec
        return spec

    def observe_ticker(
        self,
        envelope: dict[str, Any],
        *,
        transport: SourceTransport,
        channel: str,
        observed_at: datetime | None = None,
    ) -> TickerObservation:
        """Normalize one ticker for the live in-memory cache without DB writes.

        Every legal ticker updates discovery state so the recommendation
        prefilter is never throttled by the persistence sampling interval.  No
        raw event, snapshot or failure row is written here; call
        ``persist_observed_ticker`` at the sampling point to store this exact
        observation.
        """
        observed = observed_at or datetime.now(UTC)
        try:
            raw, snapshot = self._normalize_ticker(
                envelope, transport=transport, channel=channel, observed_at=observed
            )
        except NormalizationError as exc:
            self._record_ticker_failure(
                exc, "TICKER_NORMALIZATION_FAILED", str(exc), observed
            )
            raise
        return TickerObservation(raw, snapshot)

    def persist_observed_ticker(self, observation: TickerObservation) -> bool:
        """Persist one previously observed ticker; the raw event is stored first."""
        new_raw = self.repository.save_raw_event(observation.raw_event)
        self.repository.save_snapshot(observation.snapshot)
        return new_raw

    def ingest_ticker(
        self,
        envelope: dict[str, Any],
        *,
        transport: SourceTransport,
        channel: str,
        observed_at: datetime | None = None,
    ):
        observed = observed_at or datetime.now(UTC)
        try:
            raw, snapshot = self._normalize_ticker(
                envelope, transport=transport, channel=channel, observed_at=observed
            )
        except NormalizationError as exc:
            self._record_ticker_failure(
                exc, "TICKER_NORMALIZATION_FAILED", str(exc), observed
            )
            raise
        self.repository.save_raw_event(raw)
        self.repository.save_snapshot(snapshot)
        return snapshot

    def _normalize_ticker(
        self,
        envelope: dict[str, Any],
        *,
        transport: SourceTransport,
        channel: str,
        observed_at: datetime,
    ) -> tuple[RawMarketEvent, Any]:
        raw = record_raw_event(
            envelope, transport=transport, channel_or_method=channel, observed_at=observed_at
        )
        data = _ticker_data(envelope)
        name = data.get("instrument_name") if isinstance(data, dict) else None
        if not isinstance(name, str) or name not in self.specs:
            failure = NormalizationError(
                "ticker references an unknown instrument specification"
            )
            failure.raw_event_id = raw.raw_event_id  # type: ignore[attr-defined]
            failure.raw_event = raw  # type: ignore[attr-defined]
            raise failure
        try:
            snapshot = normalize_ticker(envelope, raw, self.specs[name])
        except NormalizationError as exc:
            exc.raw_event_id = raw.raw_event_id  # type: ignore[attr-defined]
            exc.raw_event = raw  # type: ignore[attr-defined]
            raise
        flags = list(snapshot.quality_flags)
        timestamp = snapshot.source.exchange_timestamp_ms
        previous = self._latest_timestamp.get(name)
        if timestamp is not None and previous is not None and timestamp < previous:
            flags.append(
                QualityFlag(
                    QualityCode.TIMESTAMP_OUT_OF_ORDER,
                    QualitySeverity.BLOCK_ACTION,
                    QualityScope.SNAPSHOT,
                    "ticker exchange timestamp is older than the latest accepted ticker",
                )
            )
        if timestamp is not None:
            age = observed_at.timestamp() - timestamp / 1000
            if age > self.stale_after_seconds:
                flags.append(
                    QualityFlag(
                        QualityCode.STALE_EXCHANGE_TIMESTAMP,
                        QualitySeverity.BLOCK_ACTION,
                        QualityScope.SNAPSHOT,
                        f"ticker age {age:.3f}s exceeds stale threshold",
                    )
                )
        if flags != list(snapshot.quality_flags):
            snapshot = replace(snapshot, quality_flags=tuple(flags), actionable=False)
        if timestamp is not None and (previous is None or timestamp >= previous):
            self._latest_timestamp[name] = timestamp
        return raw, snapshot

    def ingest_lifecycle(
        self,
        envelope: dict[str, Any],
        *,
        channel: str = "instrument.state.option.BTC",
        observed_at: datetime | None = None,
    ):
        """Persist and apply one sparse lifecycle notification after REST reconciliation."""
        observed = observed_at or datetime.now(UTC)
        raw = record_raw_event(
            envelope,
            transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
            channel_or_method=channel,
            observed_at=observed,
        )
        self.repository.save_raw_event(raw)
        params = envelope.get("params")
        data = params.get("data") if isinstance(params, dict) else None
        name = data.get("instrument_name") if isinstance(data, dict) else None
        if not isinstance(name, str) or name not in self.specs:
            message = "lifecycle event requires an existing reconciled instrument spec"
            self._failure(raw.raw_event_id, "UNKNOWN_INSTRUMENT", message, observed)
            raise NormalizationError(message)
        try:
            spec = apply_lifecycle_event(envelope, raw, self.specs[name])
        except ValueError as exc:
            self._failure(raw.raw_event_id, "LIFECYCLE_NORMALIZATION_FAILED", str(exc), observed)
            raise NormalizationError(str(exc)) from exc
        self.repository.save_instrument(spec)
        self.specs[name] = spec
        return spec

    def reconcile(self, rest_client: Any, *, observed_at: datetime | None = None) -> int:
        """Reconcile catalog through public REST; no absence-to-terminal inference."""
        response = rest_client.get_instruments("BTC")
        result = response.get("result")
        if not isinstance(result, list):
            raise NormalizationError("get_instruments result must be a list")
        count = 0
        for item in result:
            if isinstance(item, dict):
                self.ingest_instrument(
                    item,
                    transport=SourceTransport.HTTPS_REST,
                    channel="public/get_instruments",
                    observed_at=observed_at,
                )
                count += 1
        return count

    async def reconcile_forever(
        self, rest_client: Any, *, interval_seconds: float, stop_event: asyncio.Event
    ) -> None:
        """Periodically reconcile the catalog until the caller signals shutdown."""
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while not stop_event.is_set():
            await asyncio.to_thread(self.reconcile, rest_client)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue

    def _record_ticker_failure(
        self,
        exc: NormalizationError,
        code: str,
        message: str,
        observed_at: datetime,
    ) -> None:
        """Keep the raw event and a typed failure row for a rejected ticker."""
        raw_event = getattr(exc, "raw_event", None)
        if raw_event is not None:
            self.repository.save_raw_event(raw_event)
        self._failure(getattr(exc, "raw_event_id", ""), code, message, observed_at)

    def _failure(
        self, raw_event_id: str, code: str, message: str, observed_at: datetime | None
    ) -> None:
        observed = observed_at or datetime.now(UTC)
        event_id = "system:" + hashlib.sha256(f"{raw_event_id}|{code}".encode()).hexdigest()
        self.repository.save_system_event(
            SystemEvent(
                event_id,
                observed,
                observed,
                "deribit.ingestion",
                "ERROR",
                code,
                message,
                (raw_event_id,),
            )
        )


def _ticker_data(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("method") == "subscription":
        params = envelope.get("params")
        return params.get("data", {}) if isinstance(params, dict) else {}
    return envelope.get("result", envelope)
