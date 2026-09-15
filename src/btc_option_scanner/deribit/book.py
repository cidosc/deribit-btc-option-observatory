"""Raw-first, fail-closed sequence tracking for ungrouped public book channels."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from btc_option_scanner.data import SqliteRepository
from btc_option_scanner.domain import SourceTransport, SystemEvent

from .parser import record_raw_event


@dataclass(frozen=True, slots=True)
class BookUpdateResult:
    raw_event_id: str
    instrument_name: str
    update_type: str
    change_id: int
    prev_change_id: int | None
    status: str
    resync_required: bool
    bid_level_count: int
    ask_level_count: int


@dataclass(slots=True)
class BookSequenceTracker:
    """Maintain one exact local book, invalidating it on any sequence gap."""

    repository: SqliteRepository
    instrument_name: str
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    last_change_id: int | None = None
    valid: bool = False

    def apply(
        self,
        envelope: dict[str, Any],
        *,
        channel: str,
        observed_at: datetime | None = None,
    ) -> BookUpdateResult:
        observed = observed_at or datetime.now(UTC)
        raw = record_raw_event(
            envelope,
            transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
            channel_or_method=channel,
            observed_at=observed,
        )
        self.repository.save_raw_event(raw)
        try:
            accepted_channels = {
                f"book.{self.instrument_name}.100ms",
                f"book.{self.instrument_name}.agg2",
                f"book.{self.instrument_name}.raw",
            }
            if channel not in accepted_channels:
                raise ValueError("tracker accepts only its ungrouped read-only book channel")
            data = _data(envelope)
            if data.get("instrument_name") != self.instrument_name:
                raise ValueError("book update instrument does not match tracker")
            update_type = data.get("type")
            if update_type not in {"snapshot", "change"}:
                raise ValueError("book type must be snapshot or change")
            change_id = _integer(data.get("change_id"), "change_id")
            prev_change_id = (
                _integer(data.get("prev_change_id"), "prev_change_id")
                if "prev_change_id" in data
                else None
            )
            bid_updates = _levels(data.get("bids"), "bids")
            ask_updates = _levels(data.get("asks"), "asks")
            if update_type == "snapshot":
                if prev_change_id is not None:
                    raise ValueError("book snapshot must not contain prev_change_id")
                _validate_snapshot_order(bid_updates, ask_updates)
                self.bids.clear()
                self.asks.clear()
                _apply_levels(self.bids, bid_updates)
                _apply_levels(self.asks, ask_updates)
                self.last_change_id = change_id
                self.valid = True
                status, resync = "SNAPSHOT_APPLIED", False
            elif not self.valid:
                status, resync = "WAITING_FOR_SNAPSHOT", True
            elif prev_change_id != self.last_change_id:
                expected = self.last_change_id
                self.bids.clear()
                self.asks.clear()
                self.last_change_id = None
                self.valid = False
                self._event(
                    raw.raw_event_id,
                    observed,
                    "BOOK_SEQUENCE_GAP",
                    f"expected prev_change_id {expected}, received {prev_change_id}",
                )
                status, resync = "SEQUENCE_GAP", True
            else:
                _apply_levels(self.bids, bid_updates)
                _apply_levels(self.asks, ask_updates)
                self.last_change_id = change_id
                status, resync = "CHANGE_APPLIED", False
            return BookUpdateResult(
                raw.raw_event_id,
                self.instrument_name,
                update_type,
                change_id,
                prev_change_id,
                status,
                resync,
                len(self.bids),
                len(self.asks),
            )
        except ValueError as exc:
            self.bids.clear()
            self.asks.clear()
            self.last_change_id = None
            self.valid = False
            self._event(raw.raw_event_id, observed, "BOOK_NORMALIZATION_FAILED", str(exc))
            raise

    def _event(
        self, raw_event_id: str, observed: datetime, code: str, message: str
    ) -> None:
        event_id = "system:" + hashlib.sha256(f"{raw_event_id}|{code}".encode()).hexdigest()
        self.repository.save_system_event(
            SystemEvent(
                event_id,
                observed,
                observed,
                "deribit.book",
                "ERROR",
                code,
                message,
                (raw_event_id,),
            )
        )


def _data(envelope: dict[str, Any]) -> dict[str, Any]:
    params = envelope.get("params")
    data = params.get("data") if isinstance(params, dict) else None
    if envelope.get("method") != "subscription" or not isinstance(data, dict):
        raise ValueError("book message must be a subscription notification")
    return data


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _decimal(value: Any, name: str, *, positive: bool) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite() or (positive and result <= 0) or (not positive and result < 0):
        raise ValueError(f"{name} is outside its allowed range")
    return result


def _levels(value: Any, name: str) -> tuple[tuple[str, Decimal, Decimal], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    result = []
    for level in value:
        if not isinstance(level, list) or len(level) != 3:
            raise ValueError(f"{name} levels must be [action, price, amount]")
        action = level[0]
        if action not in {"new", "change", "delete"}:
            raise ValueError(f"{name} action is invalid")
        price = _decimal(level[1], f"{name}.price", positive=True)
        amount = _decimal(level[2], f"{name}.amount", positive=False)
        if action != "delete" and amount <= 0:
            raise ValueError(f"{name} non-delete amount must be positive")
        result.append((action, price, amount))
    return tuple(result)


def _validate_snapshot_order(
    bids: tuple[tuple[str, Decimal, Decimal], ...],
    asks: tuple[tuple[str, Decimal, Decimal], ...],
) -> None:
    if any(action != "new" for action, _, _ in bids + asks):
        raise ValueError("book snapshot levels must use action new")
    bid_prices = [price for _, price, _ in bids]
    ask_prices = [price for _, price, _ in asks]
    if bid_prices != sorted(bid_prices, reverse=True):
        raise ValueError("book snapshot bids are not descending")
    if ask_prices != sorted(ask_prices):
        raise ValueError("book snapshot asks are not ascending")


def _apply_levels(
    book: dict[Decimal, Decimal], levels: tuple[tuple[str, Decimal, Decimal], ...]
) -> None:
    for action, price, amount in levels:
        if action == "delete":
            book.pop(price, None)
        else:
            book[price] = amount
