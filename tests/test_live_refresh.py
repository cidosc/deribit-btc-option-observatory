"""Regression tests for live recommendation refresh under silent market data.

These tests cover the P12 acceptance blockers: the original 2-second-window
starvation, decoupled in-memory cache vs sampled persistence, complete refresh
lineage, the independent recommendation cadence, silent steady-state
eligibility, strict same-window fail-closed behaviour, alert throttling, and
subscription-slot priority for the core 120-240 DTE decision range.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from btc_option_scanner.alerts import (
    REASON_CACHE_ONLY,
    REASON_ENVIRONMENT_MISMATCH,
    REASON_REFRESH_FAILED,
    REASON_REFRESH_TIMEOUT,
    REASON_SNAPSHOT_SKEW,
    REASON_SNAPSHOT_STALE,
    LiveRecommendationSettings,
    build_refresh_prefilter_policy,
    build_refresh_set,
    evaluate_live_recommendations,
    select_coherent_snapshots,
    select_coherent_snapshots_with_diagnostics,
)
from btc_option_scanner.collector import (
    CollectorSettings,
    run_collector,
    select_collection_universe,
    subscription_split_counts,
)
from btc_option_scanner.config import load_config
from btc_option_scanner.data import SqliteRepository
from btc_option_scanner.deribit import PublicDataSpine
from btc_option_scanner.domain import InstrumentState, OptionType, SourceTransport
from tests.live_refresh_helpers import (
    FakeRefreshRestClient,
    ScriptedWebSocketConnection,
    database_counts,
    instrument_payload,
    read_json,
    rest_ticker_envelope,
    ws_ticker_message,
)

INTERVAL_SECONDS = 0.05


def _catalog_items(core_count: int, buffer_count: int = 0) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    names: list[str] = []
    for index in range(core_count):
        name = f"BTC-26JUN27-{50000 + index * 500}-C"
        names.append(name)
        items.append(instrument_payload(name, strike=50000 + index * 500, dte_days=180.0))
    for index in range(buffer_count):
        dte = 100.0 if index % 2 == 0 else 250.0
        name = f"BTC-26JUN27-BUFFER{index}-C"
        names.append(name)
        items.append(instrument_payload(name, strike=40000 + index * 10, dte_days=dte))
    return items, names


def _settings(tmp_path: Path, **overrides: Any) -> CollectorSettings:
    values: dict[str, Any] = {
        "database_path": tmp_path / "collector.sqlite",
        "status_path": tmp_path / "status.json",
        "config_path": "config.yaml",
        "ticker_persist_interval_seconds": 60.0,
        "collection_buffer_days": 30.0,
        "status_interval_seconds": 0.05,
        "refresh_limit": 12,
        "refresh_requests_per_second": 50.0,
        "refresh_timeout_seconds": 5.0,
        "refresh_cycle_budget_seconds": 5.0,
        "refresh_max_concurrency": 12,
        "catalog_refresh_seconds": 3_600.0,
        "log_level": "WARNING",
    }
    values.update(overrides)
    return CollectorSettings(**values)


def _recommendations(tmp_path: Path, **overrides: Any) -> LiveRecommendationSettings:
    values: dict[str, Any] = {
        "output_path": tmp_path / "latest-recommendation.json",
        "interval_seconds": INTERVAL_SECONDS,
    }
    values.update(overrides)
    return LiveRecommendationSettings(**values)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _spread_burst(names: list[str]) -> list[dict[str, Any]]:
    """Initial ticker burst whose exchange timestamps scatter across many windows."""
    newest_ms = _now_ms()
    messages = []
    for index, name in enumerate(names):
        window = 0 if index < 3 else index - 2
        overrides = {"delta": 0.40} if index == 0 else {}
        messages.append(
            ws_ticker_message(name, timestamp_ms=newest_ms - window * 6_000, **overrides)
        )
    return messages


def _steady_builder(skew_seconds: float = 0.0, age_seconds: float = 0.0, delta: float = 0.85):
    def builder(name: str) -> dict[str, Any]:
        now_ms = _now_ms()
        return rest_ticker_envelope(
            name,
            timestamp_ms=now_ms - int(age_seconds * 1000) - int(skew_seconds * 1000),
            delta=delta,
        )

    return builder


def _run_scenario(
    tmp_path: Path,
    *,
    settings: CollectorSettings,
    recommendation_settings: LiveRecommendationSettings,
    rest_client: FakeRefreshRestClient,
    script: list[dict[str, Any]],
    drip_schedule: list[tuple[float, dict[str, Any]]] | None = None,
    connection_tweaks=None,
    websocket_factory=None,
    run_seconds: float = 1.5,
) -> dict[str, Any]:
    connection = ScriptedWebSocketConnection(script, drip_schedule=drip_schedule)
    if connection_tweaks is not None:
        connection_tweaks(connection)

    async def default_factory() -> ScriptedWebSocketConnection:
        connection.start()
        return connection

    async def scenario() -> dict[str, Any]:
        factory = websocket_factory or default_factory

        stop_event = asyncio.Event()
        task = asyncio.create_task(
            run_collector(
                settings,
                stop_event=stop_event,
                recommendation_settings=recommendation_settings,
                rest_client=rest_client,
                websocket_factory=factory,
            )
        )
        await asyncio.sleep(run_seconds)
        observed: dict[str, Any] = {}
        observed["database_counts"] = database_counts(settings.database_path)
        observed["status"] = read_json(settings.status_path)
        if recommendation_settings.enabled and recommendation_settings.output_path.exists():
            observed["recommendation_file"] = read_json(recommendation_settings.output_path)
        stop_event.set()
        observed["exit_code"] = await task
        return observed

    return asyncio.run(scenario())


def test_spread_initial_burst_starves_the_old_two_second_window(tmp_path) -> None:
    """Current-defect regression: 60 scattered tickers leave ~3 coherent inputs and
    the old cache-driven pipeline cannot produce an eligible candidate."""
    repository = SqliteRepository(tmp_path / "defect.sqlite")
    spine = PublicDataSpine(repository, stale_after_seconds=1_000_000)
    items, names = _catalog_items(60)
    for item in items:
        spine.ingest_instrument(
            item, transport=SourceTransport.HTTPS_REST, channel="public/get_instruments"
        )
    newest_ms = _now_ms()
    snapshots = tuple(
        spine.ingest_ticker(
            ws_ticker_message(
                name,
                timestamp_ms=newest_ms - (0 if index < 3 else index - 2) * 6_000,
                **({"delta": 0.40} if index == 0 else {}),
            ),
            transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
            channel=f"ticker.{name}.agg2",
        )
        for index, name in enumerate(names)
    )
    coherent = select_coherent_snapshots(
        snapshots,
        as_of_utc=datetime.now(UTC),
        max_snapshot_skew_ms=2_000,
        max_age_seconds=30,
    )
    assert len(coherent) == 3

    settings = LiveRecommendationSettings(output_path=tmp_path / "latest.json", top_n=3)
    result = evaluate_live_recommendations(
        coherent,
        {name: OptionType.CALL for name in names},
        config=load_config("config.yaml"),
        repository=repository,
        settings=settings,
        as_of_utc=datetime.now(UTC),
    )
    assert result.candidate_count == 0
    assert result.payload["decision_state"] == "DATA_ONLY"
    assert read_json(settings.output_path)["candidates"] == []
    repository.close()


def test_cache_updates_and_persistence_sampling_are_decoupled(tmp_path) -> None:
    """Non-due ticker updates reach the memory cache but grow no database counters."""
    items, names = _catalog_items(60)
    rest = FakeRefreshRestClient(items, _steady_builder())
    drip = [
        (0.3 + 0.005 * offset, ws_ticker_message(name, timestamp_ms=_now_ms()))
        for offset, name in enumerate(names[:30])
    ]
    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path),
        recommendation_settings=_recommendations(tmp_path, enabled=False),
        rest_client=rest,
        script=_spread_burst(names),
        drip_schedule=drip,
        run_seconds=0.8,
    )
    counts = observed["database_counts"]
    status = observed["status"]
    message_counts = status["message_counts"]
    assert message_counts["ticker_received"] == 90
    assert message_counts["ticker_persisted"] == 60
    assert status["cache_instrument_count"] == 60
    # Only the burst was persisted: 60 instrument raw events + 60 ticker raw events.
    assert counts["raw_market_events"] == 120
    assert counts["market_snapshots"] == 60


def test_refreshed_candidates_resolve_to_persisted_raw_lineage(tmp_path) -> None:
    items, names = _catalog_items(60)
    rest = FakeRefreshRestClient(items, _steady_builder())
    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path),
        recommendation_settings=_recommendations(tmp_path),
        rest_client=rest,
        script=_spread_burst(names),
        run_seconds=0.8,
    )
    payload = observed["recommendation_file"]
    assert payload["decision_state"] == "ELIGIBLE"
    assert len(payload["candidates"]) >= 3
    repository = SqliteRepository(tmp_path / "collector.sqlite")
    try:
        raw_events = {event.raw_event_id: event for event in repository.raw_events()}
        metric_rows = {
            row.entity_id: row for row in repository.derived_entity_rows("MetricSnapshot")
        }
        snapshot_rows = {
            row.market_snapshot_id: row for row in repository.snapshot_rows()
        }
        for candidate in payload["candidates"]:
            market_row = snapshot_rows[candidate["market_snapshot_id"]]
            raw_event = raw_events[market_row.raw_event_id]
            assert raw_event.source.source_channel_or_method == "public/ticker"
            metric = metric_rows[candidate["metric_snapshot_id"]]
            assert metric.parent_ids == (candidate["market_snapshot_id"],)
        run_rows = [
            row
            for row in repository.derived_entity_rows("ScannerRun")
            if row.entity_id == payload["scanner_run_id"]
        ]
        assert len(run_rows) == 1
        assert run_rows[0].as_of_utc is not None
        parents = set(run_rows[0].parent_ids)
        assert parents >= {c["market_snapshot_id"] for c in payload["candidates"]}
        assert parents >= {c["metric_snapshot_id"] for c in payload["candidates"]}
        # Every referenced market snapshot resolves onward to its raw event.
        for candidate in payload["candidates"]:
            assert metric_rows[candidate["metric_snapshot_id"]].parent_ids[0] in snapshot_rows
            assert (
                snapshot_rows[candidate["market_snapshot_id"]].raw_event_id in raw_events
            )
        parent_snapshots = [
            snapshot_rows[parent]
            for parent in run_rows[0].parent_ids
            if parent in snapshot_rows
        ]
        assert parent_snapshots
        assert run_rows[0].as_of_utc >= max(
            snapshot.observed_at_utc for snapshot in parent_snapshots
        )
    finally:
        repository.close()


def test_recommendation_loop_runs_without_ticker_callbacks(tmp_path) -> None:
    items, names = _catalog_items(60)
    rest = FakeRefreshRestClient(items, _steady_builder())
    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path),
        recommendation_settings=_recommendations(tmp_path),
        rest_client=rest,
        script=[],
        run_seconds=2.5,
    )
    status = observed["status"]
    recommendation = status["recommendation"]
    assert recommendation["runs"] >= 10
    assert recommendation["eligible_candidates"] >= 3
    assert status["message_counts"]["ticker_received"] == 0
    refresh = recommendation["refresh"]
    assert refresh["last_diagnostics"]["rotation_probe_count"] == refresh["limit"]
    assert set(rest.calls) <= set(names)
    assert len(set(rest.calls)) > refresh["limit"]


def test_silent_steady_state_keeps_producing_eligible_rounds(tmp_path) -> None:
    items, names = _catalog_items(60)
    rest = FakeRefreshRestClient(items, _steady_builder())
    newest_ms = _now_ms()
    drip = [
        (0.2 + 0.05 * index, ws_ticker_message(names[index % len(names)], timestamp_ms=newest_ms))
        for index in range(10)
    ]
    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path),
        recommendation_settings=_recommendations(tmp_path),
        rest_client=rest,
        script=_spread_burst(names),
        drip_schedule=drip,
        run_seconds=2.5,
    )
    status = observed["status"]
    recommendation = status["recommendation"]
    assert recommendation["runs"] >= 10
    assert recommendation["state"] == "ELIGIBLE"
    assert recommendation["eligible_candidates"] >= 3
    payload = observed["recommendation_file"]
    assert payload["decision_state"] == "ELIGIBLE"
    refresh = recommendation["refresh"]
    assert refresh["last_diagnostics"]["coherent_input_count"] >= 3


def test_skew_and_stale_rejections_fail_the_whole_round(tmp_path) -> None:
    items, names = _catalog_items(60)

    def skewed_builder(name: str) -> dict[str, Any]:
        now_ms = _now_ms()
        if name in {names[0], names[1]}:
            return rest_ticker_envelope(name, timestamp_ms=now_ms)
        return rest_ticker_envelope(name, timestamp_ms=now_ms - 4_000)

    # The cache keeps exactly two proxy-valid contracts so each cycle's refresh
    # set is two fresh names plus ten rotation probes arriving 4 s behind.
    newest_ms = _now_ms()
    burst = [
        ws_ticker_message(names[0], timestamp_ms=newest_ms),
        ws_ticker_message(names[1], timestamp_ms=newest_ms),
    ] + [
        ws_ticker_message(name, timestamp_ms=newest_ms, delta=0.40) for name in names[2:]
    ]
    rest = FakeRefreshRestClient(items, skewed_builder)
    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path),
        recommendation_settings=_recommendations(tmp_path),
        rest_client=rest,
        script=burst,
        run_seconds=0.8,
    )
    payload = observed["recommendation_file"]
    assert payload["decision_state"] == "DATA_ONLY"
    assert payload["unavailable_reason"] == "COHORT_SAMPLE_BELOW_POLICY"
    assert payload["candidates"] == []
    assert payload["diagnostics"]["rejected_counts"][REASON_SNAPSHOT_SKEW] >= 10
    assert payload["diagnostics"]["coherent_input_count"] == 2

    stale_items, _ = _catalog_items(60)
    stale_rest = FakeRefreshRestClient(stale_items, _steady_builder(age_seconds=40.0))
    observed_stale = _run_scenario(
        tmp_path,
        settings=_settings(
            tmp_path,
            database_path=tmp_path / "stale.sqlite",
            status_path=tmp_path / "stale-status.json",
        ),
        recommendation_settings=_recommendations(
            tmp_path, output_path=tmp_path / "stale-recommendation.json"
        ),
        rest_client=stale_rest,
        script=[],
        run_seconds=0.8,
    )
    stale_payload = observed_stale["recommendation_file"]
    assert stale_payload["unavailable_reason"] == REASON_SNAPSHOT_STALE
    assert stale_payload["candidates"] == []
    assert stale_payload["diagnostics"]["rejected_counts"][REASON_SNAPSHOT_STALE] >= 12


def test_refresh_failure_and_timeout_never_keep_old_candidates(tmp_path) -> None:
    items, names = _catalog_items(60)
    rest = FakeRefreshRestClient(items, _steady_builder(), fail_after_calls=12)
    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path),
        recommendation_settings=_recommendations(tmp_path),
        rest_client=rest,
        script=_spread_burst(names),
        run_seconds=1.0,
    )
    repository = SqliteRepository(tmp_path / "collector.sqlite")
    try:
        runs = repository.derived_entity_rows("ScannerRun")
        assert runs, "at least one early eligible run must have persisted"
        assert any('"ELIGIBLE"' in row.canonical_json for row in runs)
    finally:
        repository.close()
    payload = observed["recommendation_file"]
    assert payload["unavailable_reason"] == REASON_REFRESH_FAILED
    assert payload["candidates"] == []
    assert payload["scanner_run_id"] is None
    assert observed["status"]["recommendation"]["eligible_candidates"] == 0

    timeout_rest = FakeRefreshRestClient(items, _steady_builder(), delay_seconds=0.5)
    observed_timeout = _run_scenario(
        tmp_path,
        settings=_settings(
            tmp_path,
            database_path=tmp_path / "timeout.sqlite",
            status_path=tmp_path / "timeout-status.json",
            refresh_cycle_budget_seconds=0.15,
        ),
        recommendation_settings=_recommendations(
            tmp_path, output_path=tmp_path / "timeout-recommendation.json"
        ),
        rest_client=timeout_rest,
        script=_spread_burst(names),
        run_seconds=1.0,
    )
    timeout_payload = observed_timeout["recommendation_file"]
    assert timeout_payload["unavailable_reason"] == REASON_REFRESH_TIMEOUT
    assert timeout_payload["candidates"] == []


def test_environment_mismatch_fails_closed_without_cross_environment_requests(
    tmp_path,
) -> None:
    items, names = _catalog_items(60)
    rest = FakeRefreshRestClient(
        items, _steady_builder(), base_url="https://www.deribit.com/api/v2"
    )
    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path, websocket_url="wss://test.deribit.com/ws/api/v2"),
        recommendation_settings=_recommendations(tmp_path),
        rest_client=rest,
        script=_spread_burst(names),
        run_seconds=0.8,
    )
    payload = observed["recommendation_file"]
    assert payload["unavailable_reason"] == REASON_ENVIRONMENT_MISMATCH
    assert payload["candidates"] == []
    assert rest.calls == []


def test_heartbeat_timeout_fails_closed_without_keeping_candidates(tmp_path) -> None:
    """A heartbeat timeout must clear old candidates and send no alert.

    Uses a short heartbeat timeout so the stream emits HEARTBEAT_TIMEOUT during
    the run; the file ends in an empty fail-closed state and no alert is sent.
    """
    import yaml

    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    config["market_data"]["heartbeat_timeout_seconds"] = 0.15
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    items, names = _catalog_items(60)
    rest = FakeRefreshRestClient(items, _steady_builder())
    connection = ScriptedWebSocketConnection(_spread_burst(names))
    used = {"connected": False}

    async def factory() -> ScriptedWebSocketConnection:
        if used["connected"]:
            raise ConnectionError("network unavailable after heartbeat timeout")
        used["connected"] = True
        return connection

    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path, config_path=config_path),
        recommendation_settings=_recommendations(tmp_path),
        rest_client=rest,
        script=_spread_burst(names),
        websocket_factory=factory,
        run_seconds=1.5,
    )
    repository = SqliteRepository(tmp_path / "collector.sqlite")
    try:
        runs = repository.derived_entity_rows("ScannerRun")
        assert runs, "an eligible run must have persisted before the heartbeat timeout"
        assert any('"ELIGIBLE"' in row.canonical_json for row in runs)
    finally:
        repository.close()
    payload = observed["recommendation_file"]
    assert payload["decision_state"] == "DATA_STALE"
    assert payload["candidates"] == []
    assert payload["unavailable_reason"] in {"DISCONNECTED", "HEARTBEAT_TIMEOUT"}
    assert observed["status"]["recommendation"]["alerts_sent"] == 0
    assert observed["status"]["connected"] is False


def test_disconnected_stream_clears_candidates_and_sends_no_alert(tmp_path) -> None:
    items, names = _catalog_items(60)
    rest = FakeRefreshRestClient(items, _steady_builder())
    connection = ScriptedWebSocketConnection(_spread_burst(names))
    connection.schedule_recv_failure(0.5)

    async def failing_factory() -> ScriptedWebSocketConnection:
        connection.start()
        return connection

    def factory_after_disconnect() -> ScriptedWebSocketConnection:
        raise ConnectionError("network unavailable after disconnect")

    factory_holder = {"first": True}

    async def factory() -> ScriptedWebSocketConnection:
        if factory_holder["first"]:
            factory_holder["first"] = False
            await failing_factory()
            return connection
        return await factory_after_disconnect()

    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path),
        recommendation_settings=_recommendations(tmp_path),
        rest_client=rest,
        script=_spread_burst(names),
        websocket_factory=factory,
        run_seconds=1.2,
    )
    repository = SqliteRepository(tmp_path / "collector.sqlite")
    try:
        assert repository.derived_entity_rows("ScannerRun")
    finally:
        repository.close()
    payload = observed["recommendation_file"]
    assert payload["decision_state"] == "DATA_STALE"
    assert payload["unavailable_reason"] == "DISCONNECTED"
    assert payload["candidates"] == []
    assert observed["status"]["recommendation"]["alerts_sent"] == 0
    assert observed["status"]["connected"] is False


def test_feishu_throttle_survives_per_minute_recomputation(tmp_path, monkeypatch) -> None:
    import btc_option_scanner.alerts as alerts_module

    sent: list[str] = []

    def fake_post(settings, message: str) -> None:
        sent.append(message)

    monkeypatch.setattr(alerts_module, "_post_feishu", fake_post)
    items, names = _catalog_items(60)
    rest = FakeRefreshRestClient(items, _steady_builder())
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/test-hook-id"
    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path),
        recommendation_settings=_recommendations(tmp_path, feishu_webhook_url=webhook),
        rest_client=rest,
        script=_spread_burst(names),
        run_seconds=2.5,
    )
    assert observed["status"]["recommendation"]["runs"] >= 10
    assert len(sent) == 1
    assert observed["status"]["recommendation"]["alerts_sent"] == 1


def test_refresh_writes_are_bounded_observable_and_ws_sampling_survives(tmp_path) -> None:
    items, names = _catalog_items(60)
    rest = FakeRefreshRestClient(items, _steady_builder())
    newest_ms = _now_ms()
    drip = [
        (0.3 + 0.005 * offset, ws_ticker_message(name, timestamp_ms=newest_ms))
        for offset, name in enumerate(names[:30])
    ]
    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path),
        recommendation_settings=_recommendations(tmp_path),
        rest_client=rest,
        script=_spread_burst(names),
        drip_schedule=drip,
        run_seconds=0.8,
    )
    status = observed["status"]
    refresh = status["recommendation"]["refresh"]
    runs = status["recommendation"]["runs"]
    assert refresh["successes_total"] == refresh["writes_total"]
    assert 0 < refresh["writes_total"] <= refresh["limit"] * runs
    message_counts = status["message_counts"]
    # Ordinary WS events remain sampled: received exceeds persisted within 60 s.
    assert message_counts["ticker_persisted"] < message_counts["ticker_received"]


def test_subscription_slots_prioritize_core_dte_range_deterministically() -> None:
    items, _ = _catalog_items(40, 80)
    specs = {item["instrument_name"]: _spec_from_payload(item) for item in items}
    now = datetime.now(UTC)
    selected = select_collection_universe(
        specs,
        now=now,
        min_dte_days=Decimal("120"),
        max_dte_days=Decimal("240"),
        buffer_days=30.0,
        limit=60,
    )
    assert len(selected) == 60
    split = subscription_split_counts(
        specs, selected, now=now, min_dte_days=Decimal("120"), max_dte_days=Decimal("240")
    )
    assert split == {"core": 40, "buffer": 20}
    core_catalog = {item["instrument_name"] for item in items[:40]}
    core_selected = [name for name in selected if name in core_catalog]
    assert len(core_selected) == 40
    assert selected == select_collection_universe(
        specs,
        now=now,
        min_dte_days=Decimal("120"),
        max_dte_days=Decimal("240"),
        buffer_days=30.0,
        limit=60,
    )

    small_items, _ = _catalog_items(2, 80)
    small_specs = {item["instrument_name"]: _spec_from_payload(item) for item in small_items}
    small = select_collection_universe(
        small_specs,
        now=now,
        min_dte_days=Decimal("120"),
        max_dte_days=Decimal("240"),
        buffer_days=30.0,
        limit=30,
    )
    small_split = subscription_split_counts(
        small_specs, small, now=now, min_dte_days=Decimal("120"), max_dte_days=Decimal("240")
    )
    assert small_split == {"core": 2, "buffer": 28}
    assert small == select_collection_universe(
        small_specs,
        now=now,
        min_dte_days=Decimal("120"),
        max_dte_days=Decimal("240"),
        buffer_days=30.0,
        limit=30,
    )


def test_cache_only_when_decision_universe_is_below_cohort(tmp_path) -> None:
    items, _ = _catalog_items(2, 40)
    rest = FakeRefreshRestClient(items, _steady_builder())
    observed = _run_scenario(
        tmp_path,
        settings=_settings(tmp_path),
        recommendation_settings=_recommendations(tmp_path),
        rest_client=rest,
        script=[],
        run_seconds=0.8,
    )
    payload = observed["recommendation_file"]
    assert payload["unavailable_reason"] == REASON_CACHE_ONLY
    assert payload["candidates"] == []
    assert payload["diagnostics"]["decision_universe_count"] == 2
    assert rest.calls == []


def test_observation_split_keeps_cache_writes_failures_raw_first(tmp_path) -> None:
    """Cache observation writes nothing on success; failures keep raw evidence."""
    repository = SqliteRepository(tmp_path / "obs-split.sqlite")
    spine = PublicDataSpine(repository, stale_after_seconds=30)
    item = instrument_payload("BTC-26JUN27-50000-C", strike=50000, dte_days=180)
    spine.ingest_instrument(
        item, transport=SourceTransport.HTTPS_REST, channel="public/get_instruments"
    )
    baseline = repository.counts()
    observation = spine.observe_ticker(
        ws_ticker_message("BTC-26JUN27-50000-C", timestamp_ms=_now_ms()),
        transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
        channel="ticker.BTC-26JUN27-50000-C.agg2",
    )
    assert repository.counts() == baseline
    assert spine.persist_observed_ticker(observation) is True
    assert spine.persist_observed_ticker(observation) is False
    assert repository.counts()["market_snapshots"] == 1

    from btc_option_scanner.deribit.parser import NormalizationError

    with pytest.raises(NormalizationError):
        spine.observe_ticker(
            ws_ticker_message("BTC-UNKNOWN", timestamp_ms=_now_ms()),
            transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
            channel="ticker.BTC-UNKNOWN.agg2",
        )
    counts = repository.counts()
    assert counts["system_events"] == 1
    repository.close()


def test_refresh_prefilter_policy_is_versioned_and_wider_than_final_gates() -> None:
    config = load_config("config.yaml")
    settings = LiveRecommendationSettings()
    policy = build_refresh_prefilter_policy(config, settings)
    assert policy.min_absolute_delta == Decimal("0.75")
    assert policy.max_absolute_delta == Decimal("0.95")
    assert policy.max_relative_spread == Decimal("0.075")
    assert policy.min_dte_days == config.universe.min_dte_days
    assert policy.max_dte_days == config.universe.max_dte_days
    assert policy.version.startswith("live-refresh-prefilter@1.0.0")
    assert str(policy.max_relative_spread) in policy.version


def test_refresh_set_rotation_never_form_a_permanent_blind_spot() -> None:
    proxy = ("a", "b")
    pool = tuple(f"probe-{index}" for index in range(6))
    first, rotation = build_refresh_set(proxy, pool, limit=5, probe_offset=0)
    assert first == ("a", "b", "probe-0", "probe-1", "probe-2")
    assert rotation == 3
    later, later_rotation = build_refresh_set(proxy, pool, limit=5, probe_offset=3)
    assert later[2:] == ("probe-3", "probe-4", "probe-5")
    assert later_rotation == 3
    # Rotation wraps and eventually re-probes every pool member.
    seen: set[str] = set()
    for offset in range(6):
        names, _ = build_refresh_set((), pool, limit=12, probe_offset=offset)
        seen.update(names)
    assert seen == set(pool)
    proxy_pool = tuple(f"proxy-{index}" for index in range(20))
    large_probe_pool = tuple(f"probe-{index}" for index in range(20))
    refreshed: set[str] = set()
    for offset in range(20):
        names, probe_count = build_refresh_set(
            proxy_pool, large_probe_pool, limit=12, probe_offset=offset
        )
        assert probe_count >= 1
        refreshed.update(names)
    assert refreshed == set(proxy_pool) | set(large_probe_pool)
    with pytest.raises(ValueError):
        build_refresh_set((), pool, limit=0, probe_offset=0)


def test_diagnostics_selection_reports_skew_and_stale_rejections() -> None:
    from tests.models.factories import AS_OF, market_snapshot

    base = int(AS_OF.timestamp() * 1000) - 1_000
    snapshots = (
        market_snapshot(name="BTC-A", exchange_timestamp_ms=base),
        market_snapshot(name="BTC-B", exchange_timestamp_ms=base),
        market_snapshot(name="BTC-C", exchange_timestamp_ms=base),
        market_snapshot(name="BTC-D", exchange_timestamp_ms=base - 4_000),
        market_snapshot(name="BTC-E", exchange_timestamp_ms=base - 4_000),
    )
    selected, rejected = select_coherent_snapshots_with_diagnostics(
        snapshots,
        as_of_utc=AS_OF,
        max_snapshot_skew_ms=2_000,
        max_age_seconds=30,
    )
    assert [snapshot.instrument_name for snapshot in selected] == ["BTC-A", "BTC-B", "BTC-C"]
    assert rejected[REASON_SNAPSHOT_SKEW] == 2
    assert rejected[REASON_SNAPSHOT_STALE] == 0

    stale = tuple(
        market_snapshot(name=f"BTC-{suffix}", exchange_timestamp_ms=base - 40_000)
        for suffix in ("A", "B", "C")
    )
    selected_stale, rejected_stale = select_coherent_snapshots_with_diagnostics(
        stale,
        as_of_utc=AS_OF,
        max_snapshot_skew_ms=2_000,
        max_age_seconds=30,
    )
    assert selected_stale == ()
    assert rejected_stale[REASON_SNAPSHOT_STALE] == 3


def _spec_from_payload(item: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        instrument_name=item["instrument_name"],
        expiration_timestamp_ms=item["expiration_timestamp"],
        strike_usd_per_btc=Decimal(str(item["strike"])),
        option_type=OptionType.CALL,
        state=InstrumentState.OPEN,
    )
