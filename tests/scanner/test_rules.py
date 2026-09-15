from decimal import Decimal

from btc_option_scanner.domain import DecisionState, InstrumentState, OptionType
from btc_option_scanner.models.metrics import build_metric_bundle
from btc_option_scanner.scanner import (
    Bucket,
    CohortPolicy,
    ScanInput,
    ScannerPolicy,
    scan_candidate_analyses,
    scan_candidates,
)
from tests.models.factories import AS_OF, market_snapshot


def policy(min_cohort_size: int = 2) -> ScannerPolicy:
    cohort = CohortPolicy(
        "peer-buckets@1",
        (Bucket("delta-high", Decimal("0.9"), Decimal("1.01")),),
        (Bucket("dte-medium", Decimal("90"), Decimal("200")),),
        min_cohort_size,
    )
    return ScannerPolicy(
        "scanner-lexicographic@0.1.0-experimental",
        "universe@1",
        Decimal("90"),
        Decimal("200"),
        Decimal("1"),
        Decimal("0.10"),
        cohort,
    )


def item(name: str, ask: Decimal = Decimal("0.7475"), **kwargs: object) -> ScanInput:
    market = market_snapshot(name=name, ask=ask, **kwargs)
    return ScanInput(
        OptionType.CALL,
        market,
        build_metric_bundle(market, as_of_utc=AS_OF, config_version="cfg@1").core,
    )


def test_rule_ranking_is_deterministic_and_exposes_lexicographic_reasons() -> None:
    candidates = (item("BTC-A", Decimal("0.80")), item("BTC-B", Decimal("0.75")))
    first = scan_candidates(candidates, policy())
    second = scan_candidates(candidates, policy())
    assert [candidate.instrument_name for candidate in first] == ["BTC-A", "BTC-B"]
    assert first[1].rank == 1
    assert first[1].decision_state is DecisionState.ELIGIBLE
    assert "ENTRY_DCE" in first[1].reasons[0]
    assert first == second
    analyses = scan_candidate_analyses(candidates, policy())
    assert analyses[1].entry_dce_peer_percentile.value == Decimal("100")
    assert analyses[1].entry_dce_peer_rank.value == Decimal("1")


def test_failed_closure_for_missing_ask_stale_and_nonopen_instruments() -> None:
    missing_ask = scan_candidates((item("BTC-NOASK", ask=None),), policy(1))[0]
    stale = scan_candidates((item("BTC-STALE", stale=True),), policy(1))[0]
    non_open = scan_candidates((item("BTC-CLOSED", state=InstrumentState.DELIVERED),), policy(1))[0]
    assert missing_ask.decision_state is DecisionState.NO_EXECUTABLE_ASK
    assert stale.decision_state is DecisionState.DATA_STALE
    assert non_open.decision_state is DecisionState.DATA_ONLY
    assert all(not candidate.eligible for candidate in (missing_ask, stale, non_open))


def test_cohort_sample_below_policy_has_no_rank() -> None:
    result = scan_candidates((item("BTC-ONLY"),), policy(2))[0]
    assert result.decision_state is DecisionState.INSUFFICIENT_HISTORY
    assert result.rank is None
    assert result.cohort_size == 1


def test_cross_snapshot_time_window_fails_closed_for_every_candidate() -> None:
    candidates = (
        item("BTC-A", exchange_timestamp_ms=1000),
        item("BTC-B", exchange_timestamp_ms=1001),
    )
    results = scan_candidates(candidates, policy())
    assert all(result.decision_state is DecisionState.DATA_ONLY for result in results)
    assert all(not result.eligible for result in results)
    assert all("SNAPSHOT_TIME_MISMATCH" in result.rejected_reasons for result in results)
