"""Instrument catalog updates and low-frequency REST reconciliation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from ..domain import (
    InstrumentSpec,
    InstrumentState,
    QualityCode,
    QualityFlag,
    QualityScope,
    QualitySeverity,
    RawMarketEvent,
    SourceTransport,
)
from .parser import normalize_instrument, record_raw_event


def apply_lifecycle_event(
    payload: dict[str, Any], raw: RawMarketEvent, current: InstrumentSpec
) -> InstrumentSpec:
    """Apply the official sparse state notification to an existing catalog spec."""
    params = payload.get("params")
    data = params.get("data") if isinstance(params, dict) else payload.get("result", payload)
    if not isinstance(data, dict):
        raise ValueError("lifecycle notification data must be an object")
    name, raw_state = data.get("instrument_name"), data.get("state")
    if name != current.instrument_name or not isinstance(raw_state, str):
        raise ValueError("lifecycle notification does not match the existing instrument")
    states = {state.value.lower(): state for state in InstrumentState}
    state = states.get(raw_state.lower(), InstrumentState.UNKNOWN)
    flags = list(current.quality_flags)
    if state is InstrumentState.UNKNOWN:
        flags.append(
            QualityFlag(
                QualityCode.UNKNOWN_SCHEMA_VERSION,
                QualitySeverity.BLOCK_ACTION,
                QualityScope.INSTRUMENT,
                f"unknown lifecycle state {raw_state!r}",
            )
        )
    version_suffix = hashlib.sha256(
        f"{current.instrument_spec_version}|{raw.raw_event_id}".encode()
    ).hexdigest()[:12]
    return replace(
        current,
        state=state,
        state_raw=raw_state,
        is_active=None,
        instrument_spec_version=f"{current.instrument_spec_version}+state:{version_suffix}",
        raw_event_id=raw.raw_event_id,
        quality_flags=tuple(flags),
    )


@dataclass(slots=True)
class InstrumentLifecycle:
    """Maintains a catalog without inferring lifecycle states from absence."""

    specs: dict[str, InstrumentSpec] = field(default_factory=dict)
    terminal: set[str] = field(default_factory=set)

    def apply(
        self, payload: dict[str, Any], *, channel: str, observed_at: datetime | None = None
    ) -> InstrumentSpec:
        observed = observed_at or datetime.now(UTC)
        raw = record_raw_event(
            payload,
            transport=SourceTransport.WEBSOCKET_SUBSCRIPTION
            if channel.startswith("instrument.state")
            else SourceTransport.HTTPS_REST,
            channel_or_method=channel,
            observed_at=observed,
        )
        if channel.startswith("instrument.state"):
            params = payload.get("params")
            data = params.get("data") if isinstance(params, dict) else None
            name = data.get("instrument_name") if isinstance(data, dict) else None
            if not isinstance(name, str) or name not in self.specs:
                raise ValueError("lifecycle event requires an existing reconciled instrument spec")
            spec = apply_lifecycle_event(payload, raw, self.specs[name])
        else:
            spec = normalize_instrument(payload, raw)
        self.specs[spec.instrument_name] = spec
        if spec.state.value in {"DELIVERED", "INACTIVE", "ARCHIVIZED"}:
            self.terminal.add(spec.instrument_name)
        else:
            self.terminal.discard(spec.instrument_name)
        return spec

    def reconcile(
        self, get_instruments: Callable[[], dict[str, Any]], *, observed_at: datetime | None = None
    ) -> tuple[InstrumentSpec, ...]:
        """Apply observed catalog entries.  Missing symbols are never guessed terminal."""
        envelope = get_instruments()
        result = envelope.get("result")
        if not isinstance(result, list):
            raise ValueError("get_instruments result must be a list")
        applied: list[InstrumentSpec] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            applied.append(
                self.apply(item, channel="public/get_instruments", observed_at=observed_at)
            )
        return tuple(applied)

    def active_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, spec in self.specs.items()
            if name not in self.terminal and spec.state.value == "OPEN"
        )
