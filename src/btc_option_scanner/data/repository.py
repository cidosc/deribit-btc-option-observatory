"""SQLite evidence repository with portable, explicit SQL and idempotent inserts.

The repository exposes entity-shaped rows and uses no SQLite-specific domain
features, so a PostgreSQL adapter can retain the same repository contract.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from ..domain import (
    InstrumentSpec,
    MarketSnapshot,
    MetricSnapshot,
    PortfolioSnapshot,
    RawMarketEvent,
    RollEvaluation,
    ScannerRun,
    StressRun,
    SystemEvent,
)
from .models import DerivedEntityRow, MarketSnapshotRow

SCHEMA_VERSION = 2


def _utc_text(value: datetime) -> str:
    return value.isoformat()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, datetime):
        return {"__datetime__": _utc_text(value)}
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if hasattr(value, "value") and value.__class__.__module__.endswith("enums"):
        return value.value
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def canonical_dto_json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class SqliteRepository:
    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        busy_timeout_ms: int = 5_000,
        enable_wal: bool = True,
    ) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = Path(path) if str(path) != ":memory:" else None
        self.connection = sqlite3.connect(
            str(path), isolation_level=None, timeout=busy_timeout_ms / 1000
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        if enable_wal and self.path is not None:
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = NORMAL")
        self.migrate()

    def close(self) -> None:
        self.connection.close()

    def migrate(self) -> None:
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL)"
            )
            seen = self.connection.execute(
                "SELECT version FROM schema_migrations WHERE version = ?", (SCHEMA_VERSION,)
            ).fetchone()
            if seen:
                return
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS raw_market_events (
                    raw_event_id TEXT PRIMARY KEY,
                    payload_sha256 TEXT NOT NULL UNIQUE,
                    source_transport TEXT NOT NULL,
                    source_channel_or_method TEXT NOT NULL,
                    observed_at_utc TEXT NOT NULL,
                    ingested_at_utc TEXT NOT NULL,
                    exchange_timestamp_ms INTEGER,
                    payload BLOB,
                    payload_reference TEXT,
                    rpc_id TEXT,
                    CHECK (
                        (payload IS NOT NULL AND payload_reference IS NULL)
                        OR (payload IS NULL AND payload_reference IS NOT NULL)
                    )
                );
                CREATE TABLE IF NOT EXISTS instruments (
                    instrument_name TEXT NOT NULL,
                    instrument_spec_version TEXT NOT NULL,
                    raw_event_id TEXT NOT NULL REFERENCES raw_market_events(raw_event_id),
                    state TEXT NOT NULL,
                    observed_at_utc TEXT NOT NULL,
                    canonical_json TEXT NOT NULL,
                    PRIMARY KEY (instrument_name, instrument_spec_version)
                );
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    market_snapshot_id TEXT PRIMARY KEY,
                    instrument_name TEXT NOT NULL,
                    raw_event_id TEXT NOT NULL REFERENCES raw_market_events(raw_event_id),
                    exchange_timestamp_ms INTEGER,
                    observed_at_utc TEXT NOT NULL,
                    ingested_at_utc TEXT NOT NULL,
                    state TEXT NOT NULL,
                    actionable INTEGER NOT NULL CHECK (actionable IN (0, 1)),
                    canonical_json TEXT NOT NULL,
                    UNIQUE(raw_event_id, market_snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS market_snapshots_by_instrument_time
                    ON market_snapshots (
                        instrument_name, exchange_timestamp_ms, observed_at_utc
                    );
                CREATE TABLE IF NOT EXISTS system_events (
                    system_event_id TEXT PRIMARY KEY,
                    event_at_utc TEXT NOT NULL,
                    observed_at_utc TEXT NOT NULL,
                    component TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    event_code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    related_ids_json TEXT NOT NULL,
                    schema_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS derived_entities (
                    entity_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    parent_ids_json TEXT NOT NULL,
                    as_of_utc TEXT,
                    schema_version TEXT NOT NULL,
                    metric_version TEXT,
                    config_version TEXT,
                    canonical_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS derived_entities_by_type_time
                    ON derived_entities(entity_type, as_of_utc);
                """
            )
            self.connection.execute(
                "INSERT INTO schema_migrations(version, applied_at_utc) "
                "VALUES (?, datetime('now'))",
                (SCHEMA_VERSION,),
            )

    def save_raw_event(self, event: RawMarketEvent) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO raw_market_events(
                    raw_event_id, payload_sha256, source_transport,
                    source_channel_or_method, observed_at_utc, ingested_at_utc,
                    exchange_timestamp_ms, payload, payload_reference, rpc_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(raw_event_id) DO NOTHING""",
                (
                    event.raw_event_id,
                    event.payload_sha256,
                    event.source.source_transport.value,
                    event.source.source_channel_or_method,
                    _utc_text(event.source.observed_at_utc),
                    _utc_text(event.source.ingested_at_utc),
                    event.source.exchange_timestamp_ms,
                    event.payload
                    if isinstance(event.payload, bytes)
                    else (event.payload.encode() if isinstance(event.payload, str) else None),
                    event.payload_reference,
                    str(event.rpc_id) if event.rpc_id is not None else None,
                ),
            )
        return cursor.rowcount == 1

    def save_instrument(self, spec: InstrumentSpec) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO instruments(
                    instrument_name, instrument_spec_version, raw_event_id, state,
                    observed_at_utc, canonical_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_name, instrument_spec_version) DO NOTHING""",
                (
                    spec.instrument_name,
                    spec.instrument_spec_version,
                    spec.raw_event_id,
                    spec.state.value,
                    self._raw_observed_at(spec.raw_event_id),
                    canonical_dto_json(spec),
                ),
            )
        return cursor.rowcount == 1

    def save_snapshot(self, snapshot: MarketSnapshot) -> bool:
        raw_event_id = snapshot.raw_event_ids[0]
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO market_snapshots(
                    market_snapshot_id, instrument_name, raw_event_id,
                    exchange_timestamp_ms, observed_at_utc, ingested_at_utc,
                    state, actionable, canonical_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_snapshot_id) DO NOTHING""",
                (
                    snapshot.market_snapshot_id,
                    snapshot.instrument_name,
                    raw_event_id,
                    snapshot.source.exchange_timestamp_ms,
                    _utc_text(snapshot.source.observed_at_utc),
                    _utc_text(snapshot.source.ingested_at_utc),
                    snapshot.state.value,
                    int(snapshot.actionable),
                    canonical_dto_json(snapshot),
                ),
            )
        return cursor.rowcount == 1

    def save_system_event(self, event: SystemEvent) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO system_events(
                    system_event_id, event_at_utc, observed_at_utc, component,
                    severity, event_code, message, related_ids_json, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(system_event_id) DO NOTHING""",
                (
                    event.system_event_id,
                    _utc_text(event.event_at_utc),
                    _utc_text(event.observed_at_utc),
                    event.component,
                    event.severity,
                    event.event_code,
                    event.message,
                    json.dumps(event.related_ids),
                    event.schema_version,
                ),
            )
        return cursor.rowcount == 1

    def _save_derived(
        self, entity_id: str, entity_type: str, parent_ids: tuple[str, ...], value: Any
    ) -> bool:
        as_of = getattr(value, "as_of_utc", None) or getattr(value, "computed_at_utc", None)
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO derived_entities(
                    entity_id, entity_type, parent_ids_json, as_of_utc,
                    schema_version, metric_version, config_version, canonical_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO NOTHING""",
                (
                    entity_id,
                    entity_type,
                    json.dumps(parent_ids),
                    _utc_text(as_of) if as_of is not None else None,
                    value.schema_version,
                    getattr(value, "metric_version", None),
                    getattr(value, "config_version", None),
                    canonical_dto_json(value),
                ),
            )
        return cursor.rowcount == 1

    def save_metric(self, metric: MetricSnapshot) -> bool:
        return self._save_derived(
            metric.metric_snapshot_id, "MetricSnapshot", (metric.market_snapshot_id,), metric
        )

    def save_scanner_run(self, run: ScannerRun) -> bool:
        parents = tuple(run.market_snapshot_ids) + tuple(run.metric_snapshot_ids)
        return self._save_derived(run.scanner_run_id, "ScannerRun", parents, run)

    def save_portfolio_snapshot(self, snapshot: PortfolioSnapshot) -> bool:
        parents = (snapshot.portfolio_definition_id,) + tuple(
            leg.market_snapshot_id for leg in snapshot.legs if leg.market_snapshot_id is not None
        )
        return self._save_derived(
            snapshot.portfolio_snapshot_id, "PortfolioSnapshot", parents, snapshot
        )

    def save_roll_evaluation(self, evaluation: RollEvaluation) -> bool:
        parents = (
            evaluation.portfolio_snapshot_id,
            evaluation.old_market_snapshot_id,
            evaluation.new_market_snapshot_id,
            evaluation.old_metric_snapshot_id,
            evaluation.new_metric_snapshot_id,
        )
        return self._save_derived(
            evaluation.roll_evaluation_id, "RollEvaluation", parents, evaluation
        )

    def save_stress_run(self, run: StressRun) -> bool:
        return self._save_derived(run.stress_run_id, "StressRun", (run.portfolio_snapshot_id,), run)

    def raw_events(self) -> Iterator[RawMarketEvent]:
        from ..domain import SourceMetadata, SourceTransport

        for row in self.connection.execute(
            "SELECT * FROM raw_market_events ORDER BY observed_at_utc, raw_event_id"
        ):
            yield RawMarketEvent(
                row["raw_event_id"],
                SourceMetadata(
                    SourceTransport(row["source_transport"]),
                    row["source_channel_or_method"],
                    datetime.fromisoformat(row["observed_at_utc"]),
                    datetime.fromisoformat(row["ingested_at_utc"]),
                    row["exchange_timestamp_ms"],
                ),
                row["payload_sha256"],
                row["payload"],
                row["payload_reference"],
                row["rpc_id"],
            )

    def snapshot_rows(self, instrument_name: str | None = None) -> list[MarketSnapshotRow]:
        sql = "SELECT * FROM market_snapshots"
        params: tuple[str, ...] = ()
        if instrument_name is not None:
            sql += " WHERE instrument_name = ?"
            params = (instrument_name,)
        sql += " ORDER BY exchange_timestamp_ms, observed_at_utc"
        return [
            MarketSnapshotRow(
                row["market_snapshot_id"],
                row["instrument_name"],
                row["raw_event_id"],
                row["exchange_timestamp_ms"],
                datetime.fromisoformat(row["observed_at_utc"]),
                datetime.fromisoformat(row["ingested_at_utc"]),
                row["state"],
                bool(row["actionable"]),
                row["canonical_json"],
            )
            for row in self.connection.execute(sql, params)
        ]

    def derived_entity_rows(self, entity_type: str | None = None) -> list[DerivedEntityRow]:
        sql = "SELECT * FROM derived_entities"
        params: tuple[str, ...] = ()
        if entity_type is not None:
            sql += " WHERE entity_type = ?"
            params = (entity_type,)
        sql += " ORDER BY as_of_utc, entity_type, entity_id"
        return [
            DerivedEntityRow(
                row["entity_id"],
                row["entity_type"],
                tuple(json.loads(row["parent_ids_json"])),
                datetime.fromisoformat(row["as_of_utc"]) if row["as_of_utc"] else None,
                row["schema_version"],
                row["metric_version"],
                row["config_version"],
                row["canonical_json"],
            )
            for row in self.connection.execute(sql, params)
        ]

    def counts(self) -> dict[str, int]:
        return {
            table: int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "raw_market_events",
                "instruments",
                "market_snapshots",
                "derived_entities",
                "system_events",
            )
        }

    def prune_before(self, cutoff: datetime) -> dict[str, int]:
        """Remove expired evidence without deleting lineage retained by derived entities."""
        if cutoff.tzinfo is None or cutoff.utcoffset() != UTC.utcoffset(cutoff):
            raise ValueError("retention cutoff must be UTC-aware")
        cutoff_text = _utc_text(cutoff)
        protected_ids: set[str] = set()
        for row in self.connection.execute("SELECT parent_ids_json FROM derived_entities"):
            try:
                protected_ids.update(str(item) for item in json.loads(row[0]))
            except (json.JSONDecodeError, TypeError):
                continue
        snapshot_ids = [
            str(row[0])
            for row in self.connection.execute(
                "SELECT market_snapshot_id FROM market_snapshots WHERE observed_at_utc < ?",
                (cutoff_text,),
            )
            if str(row[0]) not in protected_ids
        ]
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            before = self.counts()
            self.connection.executemany(
                "DELETE FROM market_snapshots WHERE market_snapshot_id = ?",
                ((snapshot_id,) for snapshot_id in snapshot_ids),
            )
            self.connection.execute(
                """
                DELETE FROM instruments
                WHERE observed_at_utc < ?
                  AND EXISTS (
                    SELECT 1 FROM instruments AS newer
                    WHERE newer.instrument_name = instruments.instrument_name
                      AND (
                        newer.observed_at_utc > instruments.observed_at_utc
                        OR (
                          newer.observed_at_utc = instruments.observed_at_utc
                          AND newer.instrument_spec_version > instruments.instrument_spec_version
                        )
                      )
                  )
                """,
                (cutoff_text,),
            )
            self.connection.execute(
                "DELETE FROM system_events WHERE observed_at_utc < ?", (cutoff_text,)
            )
            self.connection.execute(
                """
                DELETE FROM raw_market_events
                WHERE observed_at_utc < ?
                  AND NOT EXISTS (
                    SELECT 1 FROM instruments
                    WHERE instruments.raw_event_id = raw_market_events.raw_event_id
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM market_snapshots
                    WHERE market_snapshots.raw_event_id = raw_market_events.raw_event_id
                  )
                """,
                (cutoff_text,),
            )
            after = self.counts()
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {name: before[name] - after[name] for name in before}

    def checkpoint(self) -> None:
        if self.path is not None:
            self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self.connection.execute("PRAGMA optimize")

    def _raw_observed_at(self, raw_event_id: str) -> str:
        row = self.connection.execute(
            "SELECT observed_at_utc FROM raw_market_events WHERE raw_event_id = ?", (raw_event_id,)
        ).fetchone()
        if row is None:
            raise ValueError("instrument must reference an already persisted raw event")
        return str(row[0])
