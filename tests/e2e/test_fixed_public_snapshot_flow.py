"""Fixed public-data acceptance path; it never opens a network connection."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from btc_option_scanner.analytics.replay import EventTimeReplay, ReplayEvent
from btc_option_scanner.application import (
    combine_snapshots,
    portfolio_snapshot,
    roll_snapshot,
    save_presentation_snapshot,
    scanner_snapshot,
    stress_snapshot,
)
from btc_option_scanner.cli import main
from btc_option_scanner.data.repository import SqliteRepository
from btc_option_scanner.deribit.parser import (
    normalize_instrument,
    normalize_ticker,
    record_raw_event,
)
from btc_option_scanner.deribit.replay import EventReplayer
from btc_option_scanner.domain import (
    DecisionState,
    OptionType,
    PortfolioDefinition,
    PortfolioLeg,
    QuantityMode,
    RunStatus,
    ScannerRun,
    SourceTransport,
    ValueState,
)
from btc_option_scanner.models import build_metric_bundle
from btc_option_scanner.portfolio import PortfolioPosition, value_portfolio
from btc_option_scanner.scanner import (
    Bucket,
    CohortPolicy,
    ScanInput,
    ScannerPolicy,
    scan_candidate_analyses,
)
from btc_option_scanner.strategy import (
    AgingPolicy,
    FeeSchedule,
    ForwardPolicy,
    RollGatePolicy,
    RollInput,
    RoundingMode,
    RoundingPolicy,
    SmilePolicy,
    StressPosition,
    StressScenario,
    evaluate_roll,
    stress_portfolio,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "deribit"
OBSERVED = datetime(2026, 9, 14, 12, tzinfo=UTC)
CONFIG = "e2e-fixture+sha256:fixed-public-snapshot"


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _record(payload: dict, channel: str):
    return record_raw_event(
        payload,
        transport=SourceTransport.HTTPS_REST,
        channel_or_method=channel,
        observed_at=OBSERVED,
        ingested_at=OBSERVED,
    )


def _policy() -> ScannerPolicy:
    return ScannerPolicy(
        "scanner-lexicographic@0.1.0-experimental",
        "e2e-universe@1",
        Decimal("90"),
        Decimal("200"),
        Decimal("1"),
        Decimal("0.10"),
        CohortPolicy(
            "e2e-peers@1",
            (Bucket("delta-high", Decimal("0.7"), Decimal("1.01")),),
            (Bucket("dte", Decimal("90"), Decimal("200")),),
            2,
        ),
    )


def test_fixed_snapshot_ingest_replay_metrics_scan_portfolio_roll_stress_and_cli(
    tmp_path, capsys
) -> None:
    """Acceptance evidence for the public, no-order, P05--P09 data path."""
    instrument_payload = _payload(
        "20260914T113335Z_mainnet_ws_get_instrument_BTC-25DEC26-20000-C.json"
    )
    ticker_payload = _payload("20260914T113335Z_mainnet_ws_ticker_BTC-25DEC26-20000-C.json")
    old_raw, ticker_raw = (
        _record(instrument_payload, "public/get_instrument"),
        _record(ticker_payload, "ticker.BTC-25DEC26-20000-C.agg2"),
    )
    old_spec = normalize_instrument(instrument_payload, old_raw)
    old_market = normalize_ticker(ticker_payload, ticker_raw, old_spec)

    # A second deterministic public snapshot creates a same-clock peer and roll target.
    new_instrument, new_ticker = (
        json.loads(json.dumps(instrument_payload)),
        json.loads(json.dumps(ticker_payload)),
    )
    new_instrument["result"].update({"instrument_name": "BTC-25DEC26-50000-C", "strike": 50000})
    new_ticker["result"].update(
        {
            "instrument_name": "BTC-25DEC26-50000-C",
            "best_bid_price": 0.29,
            "best_ask_price": 0.30,
            "mark_price": 0.295,
            "mark_iv": 90.0,
            "ask_iv": 91.0,
            "bid_iv": 89.0,
            "greeks": {**ticker_payload["result"]["greeks"], "delta": 0.8},
        }
    )
    new_raw, new_ticker_raw = (
        _record(new_instrument, "public/get_instrument"),
        _record(new_ticker, "ticker.BTC-25DEC26-50000-C.agg2"),
    )
    new_spec = normalize_instrument(new_instrument, new_raw)
    new_market = normalize_ticker(new_ticker, new_ticker_raw, new_spec)

    repository = SqliteRepository(tmp_path / "e2e.sqlite")
    for raw in (old_raw, ticker_raw, new_raw, new_ticker_raw):
        assert repository.save_raw_event(raw)
    assert repository.save_instrument(old_spec) and repository.save_instrument(new_spec)
    assert repository.save_snapshot(old_market) and repository.save_snapshot(new_market)
    assert repository.counts() == {
        "raw_market_events": 4,
        "instruments": 2,
        "market_snapshots": 2,
        "derived_entities": 0,
        "system_events": 0,
    }
    replayed: list[str] = []
    assert (
        EventReplayer(repository.raw_events()).replay(
            lambda event, _: replayed.append(event.raw_event_id)
        )
        == 4
    )
    assert set(replayed) == {
        old_raw.raw_event_id,
        ticker_raw.raw_event_id,
        new_raw.raw_event_id,
        new_ticker_raw.raw_event_id,
    }
    causal = EventTimeReplay(
        (
            ReplayEvent.catalog("old-spec", OBSERVED, old_spec, sequence=0),
            ReplayEvent.catalog("new-spec", OBSERVED, new_spec, sequence=1),
            ReplayEvent.market("old-market", old_market, sequence=2),
            ReplayEvent.market("new-market", new_market, sequence=3),
        )
    )
    assert causal.state_at(OBSERVED).market(new_market.instrument_name) == new_market

    old_metrics = build_metric_bundle(old_market, as_of_utc=OBSERVED, config_version=CONFIG).core
    new_metrics = build_metric_bundle(new_market, as_of_utc=OBSERVED, config_version=CONFIG).core
    analyses = scan_candidate_analyses(
        (
            ScanInput(OptionType.CALL, old_market, old_metrics),
            ScanInput(OptionType.CALL, new_market, new_metrics),
        ),
        _policy(),
    )
    assert all(item.evaluation.decision_state is DecisionState.ELIGIBLE for item in analyses)
    assert analyses[1].evaluation.rank == 1
    run = ScannerRun(
        "e2e-scanner",
        OBSERVED,
        (old_market.market_snapshot_id, new_market.market_snapshot_id),
        (old_metrics.metric_snapshot_id, new_metrics.metric_snapshot_id),
        "e2e-universe@1",
        CONFIG,
        0,
        RunStatus.COMPLETED,
        DecisionState.ELIGIBLE,
        tuple(item.evaluation for item in analyses),
    )

    leg = PortfolioLeg(old_market.instrument_name, Decimal("2"), old_market.market_snapshot_id)
    definition = PortfolioDefinition("e2e-portfolio", Decimal("1"), (leg,), OBSERVED, "portfolio@1")
    valuation = value_portfolio(
        definition,
        (PortfolioPosition(leg, old_market, Decimal("1.495")),),
        as_of_utc=OBSERVED,
        usd_conversion_index_price_usd_per_btc=old_market.index_price_usd_per_btc,
        config_version=CONFIG,
    )
    assert valuation.snapshot.nav_btc_mark.value == Decimal("2.4934")
    roll = evaluate_roll(
        RollInput(
            valuation.snapshot.portfolio_snapshot_id,
            Decimal("1"),
            Decimal("2"),
            old_market,
            new_market,
            old_metrics,
            new_metrics,
            old_spec,
            new_spec,
            QuantityMode.EQUAL_CONTRACTS,
            FeeSchedule("fixture-fee@1", Decimal("0.01"), "test-only fixed evidence", True),
            RoundingPolicy("fixture-round@1", RoundingMode.NEAREST_HALF_UP, Decimal("0.1")),
            AgingPolicy("fixture-aging@1", Decimal("90"), Decimal("0.95")),
            RollGatePolicy(
                "fixture-gates@1",
                1000,
                timedelta(days=7),
                Decimal("0.1"),
                Decimal("0.05"),
                Decimal("0.1"),
                Decimal("1"),
                Decimal("1"),
                Decimal("3"),
            ),
            OBSERVED,
            CONFIG,
            ValueState.available(Decimal("0.2")),
        )
    )
    assert roll.evaluation.decision_state is DecisionState.ROLL_CANDIDATE
    stress = stress_portfolio(
        definition,
        (StressPosition(leg, old_market),),
        portfolio_snapshot_id=valuation.snapshot.portfolio_snapshot_id,
        as_of_utc=OBSERVED,
        scenarios=(
            StressScenario(
                "spot-down",
                Decimal("-0.2"),
                Decimal("30"),
                Decimal("-10"),
                Decimal("0"),
                ForwardPolicy.PRESERVE_BASIS_RATIO,
                SmilePolicy.STICKY_STRIKE_PARALLEL,
                "rate@1",
                "forward@1",
                "smile@1",
            ),
        ),
        config_version=CONFIG,
    )
    assert stress.results[0].conditional_label == "MODEL_CONDITIONAL_NOT_EXECUTABLE_REPRICE"

    # Derived evidence rows retain the immutable parent-ID chain back to both market snapshots.
    assert repository.save_metric(old_metrics)
    assert repository.save_metric(new_metrics)
    assert repository.save_scanner_run(run)
    assert repository.save_portfolio_snapshot(valuation.snapshot)
    assert repository.save_roll_evaluation(roll.evaluation)
    assert repository.save_stress_run(stress.stress_run)
    derived = repository.derived_entity_rows()
    assert len(derived) == 6
    scanner_row = next(row for row in derived if row.entity_type == "ScannerRun")
    assert set(scanner_row.parent_ids) == {
        old_market.market_snapshot_id,
        new_market.market_snapshot_id,
        old_metrics.metric_snapshot_id,
        new_metrics.metric_snapshot_id,
    }
    assert scanner_row.config_version == CONFIG

    # CLI is strictly a serialization view of application results, not a recalculation path.
    snapshot = combine_snapshots(
        scanner_snapshot(run, analyses),
        portfolio_snapshot(valuation),
        roll_snapshot((roll,)),
        stress_snapshot(stress),
    )
    sections = snapshot.sections
    output = tmp_path / "presentation.json"
    save_presentation_snapshot(snapshot, output)
    for command, section_name in (
        ("scan", "scanner"),
        ("portfolio", "portfolio"),
        ("roll", "roll"),
        ("stress", "stress"),
    ):
        assert main(["--snapshot", str(output), command]) == 0
        assert json.loads(capsys.readouterr().out)["result"] == sections[section_name]
    assert main(["--snapshot", str(output), "inspect", old_market.instrument_name]) == 0
    inspected = json.loads(capsys.readouterr().out)["result"]
    assert inspected["records"][0]["instrument_name"] == old_market.instrument_name
    repository.close()
