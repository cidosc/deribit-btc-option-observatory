"""Bounded, public-only Deribit REST collector soak with JSON evidence output."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from btc_option_scanner.data import SqliteRepository
from btc_option_scanner.deribit import DeribitPublicRestClient, PublicDataSpine, RateLimitConfig
from btc_option_scanner.domain import SourceTransport


def _instrument(items: list[dict], now: datetime) -> dict:
    cutoff_ms = int((now + timedelta(days=1)).timestamp() * 1000)
    eligible = [
        item
        for item in items
        if item.get("state") == "open"
        and isinstance(item.get("expiration_timestamp"), int)
        and item["expiration_timestamp"] > cutoff_ms
    ]
    if not eligible:
        raise RuntimeError("no open BTC option with more than one day to expiry")
    return min(eligible, key=lambda item: (item["expiration_timestamp"], item["instrument_name"]))


def run(duration_seconds: float, interval_seconds: float, database: Path, report: Path) -> int:
    if duration_seconds <= 0 or interval_seconds <= 0:
        raise ValueError("duration and interval must be positive")
    started = datetime.now(UTC)
    client = DeribitPublicRestClient(
        config=RateLimitConfig(requests_per_second=1 / interval_seconds, timeout_seconds=10)
    )
    repository = SqliteRepository(database)
    spine = PublicDataSpine(repository, stale_after_seconds=30)
    initial = repository.counts()
    attempted = normalized = actionable = 0
    errors: list[str] = []
    chosen = "UNAVAILABLE"
    try:
        catalog = client.get_instruments("BTC")
        items = catalog.get("result")
        if not isinstance(items, list):
            raise RuntimeError("public/get_instruments result is not a list")
        spec_payload = _instrument([item for item in items if isinstance(item, dict)], started)
        chosen = spec_payload["instrument_name"]
        spine.ingest_instrument(
            spec_payload,
            transport=SourceTransport.HTTPS_REST,
            channel="public/get_instruments",
        )
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            attempted += 1
            try:
                snapshot = spine.ingest_ticker(
                    client.ticker(chosen),
                    transport=SourceTransport.HTTPS_REST,
                    channel="public/ticker",
                )
                normalized += 1
                actionable += int(snapshot.actionable)
            except Exception as exc:  # evidence must retain per-sample failures
                errors.append(f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        final = repository.counts()
        repository.close()

    finished = datetime.now(UTC)
    new_raw = final["raw_market_events"] - initial["raw_market_events"]
    new_snapshots = final["market_snapshots"] - initial["market_snapshots"]
    success = attempted > 0 and normalized == attempted and not errors
    evidence = {
        "status": "PASS" if success else "FAIL",
        "public_only": True,
        "instrument_name": chosen,
        "started_at_utc": started.isoformat(),
        "finished_at_utc": finished.isoformat(),
        "requested_duration_seconds": duration_seconds,
        "interval_seconds": interval_seconds,
        "samples_attempted": attempted,
        "samples_normalized": normalized,
        "actionable_samples": actionable,
        "new_raw_events": new_raw,
        "new_market_snapshots": new_snapshots,
        "deduplicated_samples": normalized - new_snapshots,
        "errors": errors,
        "database": str(database.resolve()),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, default=20)
    parser.add_argument("--interval-seconds", type=float, default=2)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    return run(args.duration_seconds, args.interval_seconds, args.database, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
