"""Bounded public Mainnet scanner-lite steady-state soak with JSON evidence.

Runs the real collector (public WebSocket + public REST refresh, no auth, no
books) for a configured duration, separates the initial warm-up window from the
steady state, and records per-cycle telemetry: subscription counts, cache size,
prefilter counts, refresh requests/successes and durations, coherent inputs,
eligible candidates, rejection reasons, message counts, database growth, and
disconnect counts.  No credentials are used or required.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from btc_option_scanner.alerts import LiveRecommendationSettings
from btc_option_scanner.collector import CollectorSettings, run_collector


def _database_counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        tables = (
            "raw_market_events",
            "instruments",
            "market_snapshots",
            "derived_entities",
            "system_events",
        )
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
    finally:
        connection.close()


def _system_event_counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT event_code, COUNT(*) FROM system_events GROUP BY event_code"
        ).fetchall()
        return {str(code): int(count) for code, count in rows}
    finally:
        connection.close()


async def soak(
    *,
    duration_seconds: float,
    warmup_seconds: float,
    workdir: Path,
    report: Path,
) -> dict:
    started_at = datetime.now(UTC)
    settings = CollectorSettings(
        database_path=workdir / "soak.sqlite",
        status_path=workdir / "soak-status.json",
        config_path="config.yaml",
        status_interval_seconds=5.0,
        log_level="WARNING",
    )
    recommendation_settings = LiveRecommendationSettings(
        output_path=workdir / "soak-recommendation.json"
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        run_collector(
            settings,
            stop_event=stop_event,
            recommendation_settings=recommendation_settings,
        )
    )
    deadline = asyncio.get_running_loop().time() + duration_seconds
    timeline: list[dict] = []
    previous_runs = 0
    while not stop_event.is_set():
        await asyncio.sleep(5.0)
        if not (workdir / "soak-status.json").exists():
            if task.done():
                break
            continue
        status = json.loads((workdir / "soak-status.json").read_text("utf-8"))
        elapsed = (datetime.now(UTC) - started_at).total_seconds()
        runs = status["recommendation"]["runs"]
        sample = {
            "elapsed_seconds": round(elapsed, 1),
            "phase": "warmup" if elapsed <= warmup_seconds else "steady_state",
            "connected": status["connected"],
            "selected_instrument_count": status["selected_instrument_count"],
            "subscription_split": status.get("subscription_split"),
            "cache_instrument_count": status.get("cache_instrument_count"),
            "ticker_received": status["message_counts"]["ticker_received"],
            "ticker_persisted": status["message_counts"]["ticker_persisted"],
            "recommendation_state": status["recommendation"]["state"],
            "recommendation_runs": runs,
            "eligible_candidates": status["recommendation"]["eligible_candidates"],
            "refresh_requests_total": status["recommendation"]["refresh"]["requests_total"],
            "refresh_successes_total": status["recommendation"]["refresh"]["successes_total"],
            "last_refresh_diagnostics": status["recommendation"]["refresh"].get(
                "last_diagnostics", {}
            ),
            "last_refresh_reason": status["recommendation"]["refresh"].get("last_reason"),
            "database_size_bytes": status["database_size_bytes"],
        }
        if runs > previous_runs:
            sample["cycle_completed"] = True
            previous_runs = runs
        timeline.append(sample)
        if asyncio.get_running_loop().time() >= deadline:
            break
    stop_event.set()
    exit_code = await task
    finished_at = datetime.now(UTC)
    status = json.loads((workdir / "soak-status.json").read_text("utf-8"))
    counts_start = _database_counts(workdir / "soak.sqlite")
    cycles = [sample for sample in timeline if sample.get("cycle_completed")]
    warmup_cycles = [cycle for cycle in cycles if cycle["phase"] == "warmup"]
    steady_cycles = [cycle for cycle in cycles if cycle["phase"] == "steady_state"]
    disconnect_events = sum(
        count
        for code, count in _system_event_counts(workdir / "soak.sqlite").items()
        if code in {"DISCONNECTED", "HEARTBEAT_TIMEOUT"}
    )
    database_growth_bytes = status["database_size_bytes"]
    evidence = {
        "schema": "scanner-lite-steady-soak-v1",
        "status": "PASS" if exit_code == 0 and not status.get("fatal_error") else "FAIL",
        "public_only": True,
        "environment": "mainnet",
        "network_access": "required",
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": finished_at.isoformat(),
        "requested_duration_seconds": duration_seconds,
        "warmup_seconds": warmup_seconds,
        "actual_duration_seconds": round((finished_at - started_at).total_seconds(), 1),
        "profile": settings.collection_profile,
        "websocket_url": settings.websocket_url,
        "rest_base_url_from_websocket": "https://www.deribit.com/api/v2",
        "fatal_error": status.get("fatal_error"),
        "exit_code": exit_code,
        "database_counts_final": counts_start,
        "database_size_bytes_final": status["database_size_bytes"],
        "database_growth_bytes_total": database_growth_bytes,
        "estimated_growth_bytes_per_day": status.get(
            "estimated_database_growth_bytes_per_day"
        ),
        "disconnect_events": disconnect_events,
        "cycles_completed_total": status["recommendation"]["runs"],
        "warmup_cycle_count": len(warmup_cycles),
        "steady_state_cycle_count": len(steady_cycles),
        "steady_state_eligible_cycle_count": len(
            [cycle for cycle in steady_cycles if cycle["eligible_candidates"] > 0]
        ),
        "final_recommendation_state": status["recommendation"]["state"],
        "final_eligible_candidates": status["recommendation"]["eligible_candidates"],
        "cycles": cycles,
        "timeline": timeline,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in evidence.items() if key not in {"cycles", "timeline"}},
            indent=2,
            sort_keys=True,
        )
    )
    return evidence


def verify_lineage(database: Path, report: Path) -> dict:
    """Walk the last ELIGIBLE run's IDs back to persisted raw evidence."""
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT as_of_utc, canonical_json FROM derived_entities "
            "WHERE entity_type='ScannerRun' ORDER BY as_of_utc"
        ).fetchall()
        run = None
        for _as_of, blob in rows:
            candidate = json.loads(blob)
            if candidate.get("decision_state") == "ELIGIBLE":
                run = candidate
        if run is None:
            raise RuntimeError("no ELIGIBLE scanner run persisted")
        market_ids = run["market_snapshot_ids"]
        metric_ids = run["metric_snapshot_ids"]
        metric_parent = {
            row[0]: json.loads(row[1])
            for row in connection.execute(
                "SELECT entity_id, parent_ids_json FROM derived_entities "
                "WHERE entity_type='MetricSnapshot'"
            ).fetchall()
        }
        snapshots = dict(
            connection.execute(
                "SELECT market_snapshot_id, raw_event_id FROM market_snapshots"
            ).fetchall()
        )
        channels = dict(
            connection.execute(
                "SELECT raw_event_id, source_channel_or_method FROM raw_market_events"
            ).fetchall()
        )
        metric_resolution = {
            metric_id: metric_parent.get(metric_id, [None])[0] == market_id
            for metric_id, market_id in zip(metric_ids, market_ids, strict=True)
        }
        raw_resolution = {
            market_id: (
                snapshots.get(market_id) in channels
                and channels[snapshots.get(market_id)] == "public/ticker"
            )
            for market_id in market_ids
        }
        metric_complete = all(metric_resolution.values())
        raw_complete = all(raw_resolution.values())
        evidence = {
            "schema": "live-lineage-verification-v1",
            "status": "PASS" if metric_complete and raw_complete else "FAIL",
            "scanner_run_id": run["scanner_run_id"],
            "as_of_utc": run.get("as_of_utc"),
            "decision_state": run["decision_state"],
            "market_snapshot_ids": market_ids,
            "metric_snapshot_ids": metric_ids,
            "metric_resolutions": metric_resolution,
            "raw_resolutions": raw_resolution,
        }
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
        print(
            json.dumps(
                {
                    key: value
                    for key, value in evidence.items()
                    if not key.startswith("metric_resolutions")
                    and key != "raw_resolutions"
                },
                indent=2,
                sort_keys=True,
            )
        )
        return evidence
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-lineage", action="store_true")
    parser.add_argument("--database", type=Path, default=Path("runtime/soak/soak.sqlite"))
    parser.add_argument("--duration-seconds", type=float, default=780.0)
    parser.add_argument("--warmup-seconds", type=float, default=60.0)
    parser.add_argument("--workdir", type=Path, default=Path("runtime/soak"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.verify_lineage:
        evidence = verify_lineage(args.database, args.report)
        return 0 if evidence["status"] == "PASS" else 1
    args.workdir.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        candidate = args.workdir / f"soak.sqlite{suffix}"
        if candidate.exists():
            candidate.unlink()
    evidence = asyncio.run(
        soak(
            duration_seconds=args.duration_seconds,
            warmup_seconds=args.warmup_seconds,
            workdir=args.workdir,
            report=args.report,
        )
    )
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
