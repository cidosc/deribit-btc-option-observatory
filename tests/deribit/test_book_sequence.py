from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from btc_option_scanner.data import SqliteRepository
from btc_option_scanner.deribit import BookSequenceTracker

OBSERVED = datetime(2026, 9, 15, tzinfo=UTC)
CHANNEL = "book.BTC-X.100ms"


def _message(
    update_type: str,
    change_id: int,
    *,
    prev_change_id: int | None = None,
) -> dict:
    data = {
        "type": update_type,
        "timestamp": 1790000000000 + change_id,
        "instrument_name": "BTC-X",
        "change_id": change_id,
        "bids": [["new", 0.10, 1.0]],
        "asks": [["new", 0.11, 2.0]],
    }
    if prev_change_id is not None:
        data["prev_change_id"] = prev_change_id
    return {
        "jsonrpc": "2.0",
        "method": "subscription",
        "params": {"channel": CHANNEL, "data": data},
    }


def test_book_snapshot_increment_gap_and_snapshot_recovery_are_raw_first() -> None:
    repository = SqliteRepository()
    tracker = BookSequenceTracker(repository, "BTC-X")
    snapshot = tracker.apply(_message("snapshot", 10), channel=CHANNEL, observed_at=OBSERVED)
    assert snapshot.status == "SNAPSHOT_APPLIED"

    change_payload = _message("change", 11, prev_change_id=10)
    change_payload["params"]["data"]["bids"] = [["change", 0.10, 3.0]]
    change_payload["params"]["data"]["asks"] = [["delete", 0.11, 0.0]]
    change = tracker.apply(change_payload, channel=CHANNEL, observed_at=OBSERVED)
    assert change.status == "CHANGE_APPLIED"
    assert tracker.bids and not tracker.asks

    gap_payload = deepcopy(change_payload)
    gap_payload["params"]["data"].update({"prev_change_id": 999, "change_id": 12})
    gap = tracker.apply(gap_payload, channel=CHANNEL, observed_at=OBSERVED)
    assert gap.status == "SEQUENCE_GAP" and gap.resync_required
    assert not tracker.valid and not tracker.bids and not tracker.asks

    waiting_payload = deepcopy(change_payload)
    waiting_payload["params"]["data"].update({"prev_change_id": 12, "change_id": 13})
    waiting = tracker.apply(waiting_payload, channel=CHANNEL, observed_at=OBSERVED)
    assert waiting.status == "WAITING_FOR_SNAPSHOT"

    recovered = tracker.apply(_message("snapshot", 20), channel=CHANNEL, observed_at=OBSERVED)
    assert recovered.status == "SNAPSHOT_APPLIED" and tracker.valid
    assert repository.counts()["raw_market_events"] == 5
    assert repository.counts()["system_events"] == 1
    repository.close()


def test_book_snapshot_order_is_validated_without_silent_sorting() -> None:
    repository = SqliteRepository()
    tracker = BookSequenceTracker(repository, "BTC-X")
    payload = _message("snapshot", 10)
    payload["params"]["data"]["bids"] = [
        ["new", 0.09, 1.0],
        ["new", 0.10, 1.0],
    ]
    try:
        tracker.apply(payload, channel=CHANNEL, observed_at=OBSERVED)
    except ValueError as exc:
        assert "not descending" in str(exc)
    else:
        raise AssertionError("unsorted book snapshot must fail")
    assert repository.counts()["raw_market_events"] == 1
    assert not tracker.valid
    repository.close()


def test_recorded_mainnet_increment_and_injected_gap_fixtures() -> None:
    fixture_root = Path(__file__).parents[1] / "fixtures" / "deribit" / "20260915_ws_soak"
    snapshot_path = next(fixture_root.glob("*_mainnet_ws_book_100ms_snapshot_*.json"))
    increment_path = next(fixture_root.glob("*_mainnet_ws_book_100ms_increment_*.json"))
    gap_path = next(fixture_root.glob("*_synthetic_book_sequence_gap_*.json"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    increment = json.loads(increment_path.read_text(encoding="utf-8"))
    gap = json.loads(gap_path.read_text(encoding="utf-8"))
    instrument = snapshot["params"]["data"]["instrument_name"]
    channel = snapshot["params"]["channel"]

    repository = SqliteRepository()
    tracker = BookSequenceTracker(repository, instrument)
    snapshot_result = tracker.apply(snapshot, channel=channel, observed_at=OBSERVED)
    increment_result = tracker.apply(increment, channel=channel, observed_at=OBSERVED)
    assert snapshot_result.status == "SNAPSHOT_APPLIED"
    assert increment_result.status == "CHANGE_APPLIED"

    gap_tracker = BookSequenceTracker(repository, instrument)
    gap_tracker.apply(snapshot, channel=channel, observed_at=OBSERVED)
    result = gap_tracker.apply(gap, channel=channel, observed_at=OBSERVED)
    assert result.status == "SEQUENCE_GAP" and result.resync_required
    assert not gap_tracker.valid
    repository.close()
