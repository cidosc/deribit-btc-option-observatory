from datetime import timedelta
from decimal import Decimal

from btc_option_scanner.analytics.iv_history import IVHistoryPolicy, evaluate_iv_history
from btc_option_scanner.domain import Availability, IVObservation, PriceBasis
from tests.models.factories import AS_OF


def observation(name: str, iv: str, days: int) -> IVObservation:
    return IVObservation(
        market_snapshot_id=f"m-{name}-{days}",
        instrument_name=name,
        as_of_utc=AS_OF + timedelta(days=days),
        metric_side=PriceBasis.ENTRY_ASK,
        iv_annual_decimal=Decimal(iv),
        delta_bucket_id="d90",
        dte_bucket_id="t120",
        bucket_policy_version="iv-buckets@1",
        config_version="cfg@1",
    )


def policy() -> IVHistoryPolicy:
    return IVHistoryPolicy(
        "iv-window@1",
        min_observations=3,
        expected_observations=4,
        min_coverage_ratio=Decimal("0.75"),
    )


def test_iv_history_filters_future_and_other_sides_then_computes_statistics() -> None:
    target = observation("target", "0.50", 10)
    other_side = IVObservation(
        market_snapshot_id="m-bid",
        instrument_name="bid",
        as_of_utc=AS_OF,
        metric_side=PriceBasis.LIQUIDATION_BID,
        iv_annual_decimal=Decimal("9"),
        delta_bucket_id="d90",
        dte_bucket_id="t120",
        bucket_policy_version="iv-buckets@1",
        config_version="cfg@1",
    )
    result = evaluate_iv_history(
        target,
        (
            observation("a", "0.20", 1),
            observation("b", "0.40", 2),
            observation("c", "0.60", 3),
            observation("future", "0.99", 11),
            other_side,
        ),
        policy(),
    )
    assert result.observation_count == 3
    assert result.coverage_ratio == Decimal("0.75")
    assert result.warm_up_complete is True
    assert result.percentile.value == Decimal("66.66666666666666666666666667")
    assert result.rank.value == Decimal("2")
    assert result.z_score.availability is Availability.AVAILABLE


def test_warmup_never_invents_percentile_and_zero_std_has_explicit_z_failure() -> None:
    target = observation("target", "0.50", 10)
    short = evaluate_iv_history(target, (observation("a", "0.20", 1),), policy())
    assert short.warm_up_complete is False
    assert short.percentile.availability is Availability.UNAVAILABLE
    equal = evaluate_iv_history(
        target,
        (observation("a", "0.50", 1), observation("b", "0.50", 2), observation("c", "0.50", 3)),
        policy(),
    )
    assert equal.warm_up_complete is True
    assert equal.z_score.availability is Availability.UNAVAILABLE
    assert equal.z_score.reason.code == "ZERO_STANDARD_DEVIATION"
