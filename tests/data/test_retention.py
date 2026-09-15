from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from btc_option_scanner.data import SqliteRepository
from btc_option_scanner.deribit import PublicDataSpine
from btc_option_scanner.deribit.parser import record_raw_event
from btc_option_scanner.domain import SourceTransport


def _fixture(name: str) -> dict:
    return json.loads(
        (Path(__file__).parents[1] / "fixtures" / "deribit" / name).read_text(encoding="utf-8")
    )


def test_file_repository_enables_wal_busy_timeout_and_lineage_safe_retention(tmp_path) -> None:
    database = tmp_path / "collector.sqlite"
    repository = SqliteRepository(database)
    assert repository.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert repository.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    old = datetime.now(UTC) - timedelta(days=10)
    spine = PublicDataSpine(repository, stale_after_seconds=1_000_000)
    instrument = _fixture("20260914T113335Z_mainnet_ws_get_instrument_BTC-25DEC26-20000-C.json")
    ticker = _fixture("20260914T113336Z_mainnet_ws_ticker_agg2_BTC-25DEC26-20000-C.json")
    spine.ingest_instrument(
        instrument,
        transport=SourceTransport.HTTPS_REST,
        channel="public/get_instrument",
        observed_at=old,
    )
    protected = spine.ingest_ticker(
        ticker,
        transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
        channel="ticker.BTC-25DEC26-20000-C.agg2",
        observed_at=old,
    )
    repository.connection.execute(
        """
        INSERT INTO derived_entities(
            entity_id, entity_type, parent_ids_json, as_of_utc,
            schema_version, metric_version, config_version, canonical_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "derived-protection-probe",
            "Probe",
            json.dumps([protected.market_snapshot_id]),
            old.isoformat(),
            "domain-schema-v1",
            None,
            None,
            "{}",
        ),
    )
    orphan = record_raw_event(
        {"orphan": True},
        transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
        channel_or_method="book.BTC-X.100ms",
        observed_at=old,
    )
    repository.save_raw_event(orphan)

    deleted = repository.prune_before(datetime.now(UTC) - timedelta(days=1))
    assert deleted["raw_market_events"] == 1
    assert repository.snapshot_rows()[0].market_snapshot_id == protected.market_snapshot_id
    assert all(event.raw_event_id != orphan.raw_event_id for event in repository.raw_events())
    repository.close()
