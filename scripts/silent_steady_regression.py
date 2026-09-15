"""Offline silent-market steady-state regression with machine-readable evidence.

Runs the collector against fixed fixture tickers and a scripted (non-network)
refresh client: an initial 60-instrument burst whose exchange timestamps are
scattered across many 2-second windows, then a quiet WebSocket.  The
recommendation loop must keep producing ELIGIBLE rounds through its independent
active refresh instead of starving into fixed 3-input DATA_ONLY rounds.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402

import tests.test_live_refresh as fixtures  # noqa: E402
from btc_option_scanner.collector import run_collector  # noqa: E402
from tests.live_refresh_helpers import (  # noqa: E402
    FakeRefreshRestClient,
    ScriptedWebSocketConnection,
    database_counts,
    read_json,
)


async def run_scenario(
    *,
    cycles_requested: int,
    interval_seconds: float,
    database_path: Path,
    report_path: Path,
) -> dict:
    started_at = datetime.now(UTC)
    items, names = fixtures._catalog_items(60)
    rest = FakeRefreshRestClient(items, fixtures._steady_builder())
    connection = ScriptedWebSocketConnection(fixtures._spread_burst(names))

    async def factory() -> ScriptedWebSocketConnection:
        connection.start()
        return connection

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        run_collector(
            fixtures._settings(
                database_path.parent,
                database_path=database_path,
                status_path=database_path.parent / "status.json",
            ),
            stop_event=stop_event,
            recommendation_settings=fixtures._recommendations(
                database_path.parent, interval_seconds=interval_seconds
            ),
            rest_client=rest,
            websocket_factory=factory,
        )
    )

    samples: list[dict] = []
    previous_runs = 0
    while not stop_event.is_set():
        await asyncio.sleep(min(0.1, interval_seconds))
        status_path = database_path.parent / "status.json"
        if not status_path.exists():
            if task.done():
                break
            continue
        status = read_json(status_path)
        runs = status["recommendation"]["runs"]
        sample = {
            "sampled_at_utc": datetime.now(UTC).isoformat(),
            "recommendation_runs": runs,
            "state": status["recommendation"]["state"],
            "eligible_candidates": status["recommendation"]["eligible_candidates"],
            "coherent_input_count": status["recommendation"]["refresh"][
                "coherent_input_count"
            ],
            "ticker_received": status["message_counts"]["ticker_received"],
            "ticker_persisted": status["message_counts"]["ticker_persisted"],
            "connected": status["connected"],
        }
        if runs > previous_runs:
            sample["cycle_diagnostics"] = status["recommendation"]["refresh"].get(
                "last_diagnostics", {}
            )
            previous_runs = runs
        samples.append(sample)
        if runs >= cycles_requested:
            break
        if task.done() and task.exception():
            break
    stop_event.set()
    exit_code = await task
    final_status = read_json(database_path.parent / "status.json")

    cycles = [
        sample
        for sample in samples
        if "cycle_diagnostics" in sample and sample["recommendation_runs"] > 0
    ]
    warmup = cycles[:3]
    steady = cycles[3:]
    steady_eligible = [
        cycle
        for cycle in steady
        if cycle["state"] == "ELIGIBLE" and cycle["eligible_candidates"] >= 1
    ]
    coherent_inputs = [
        cycle["cycle_diagnostics"].get("coherent_input_count", 0) for cycle in steady
    ]
    success = (
        exit_code == 0
        and final_status["recommendation"]["runs"] >= cycles_requested
        and len(steady) > 0
        and len(steady_eligible) == len(steady)
        and bool(coherent_inputs)
        and all(count >= 4 for count in coherent_inputs)
    )
    evidence = {
        "schema": "silent-steady-regression-v1",
        "status": "PASS" if success else "FAIL",
        "public_only": True,
        "network_access": "none",
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "recommendation_interval_seconds": interval_seconds,
        "cycles_requested": cycles_requested,
        "cycles_completed": final_status["recommendation"]["runs"],
        "initial_burst": {
            "instrument_count": len(names),
            "exchange_timestamp_windows": 57,
            "coherent_inputs_after_burst": 3,
            "note": (
                "60 fixture tickers with exchange timestamps spread over ~5.7 minutes; "
                "the old 2-second cache-only pipeline starved to about 3 inputs"
            ),
        },
        "warmup_cycles": warmup,
        "steady_state_cycles": steady,
        "steady_state_summary": {
            "cycles": len(steady),
            "eligible_cycles": len(steady_eligible),
            "coherent_inputs_min": min(coherent_inputs) if coherent_inputs else None,
            "coherent_inputs_max": max(coherent_inputs) if coherent_inputs else None,
            "no_longer_structurally_fixed_to_three_inputs": (
                max(coherent_inputs) > 3 if coherent_inputs else False
            ),
        },
        "final_recommendation_state": final_status["recommendation"]["state"],
        "final_database_counts": database_counts(database_path),
        "database_size_bytes": final_status["database_size_bytes"],
        "refresh_totals": final_status["recommendation"]["refresh"],
        "exit_code": exit_code,
        "samples": samples,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: value for key, value in evidence.items() if key != "samples"},
                     indent=2, sort_keys=True))
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--interval-seconds", type=float, default=0.05)
    parser.add_argument("--database", type=Path, default=Path("runtime/silent-regression.sqlite"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    for stale in (
        args.database,
        args.database.parent / "status.json",
        args.database.parent / "latest-recommendation.json",
    ):
        if stale.exists():
            stale.unlink()
    evidence = asyncio.run(
        run_scenario(
            cycles_requested=args.cycles,
            interval_seconds=args.interval_seconds,
            database_path=args.database,
            report_path=args.report,
        )
    )
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
