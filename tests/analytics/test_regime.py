from datetime import timedelta
from decimal import Decimal

from btc_option_scanner.analytics.iv_history import IVHistoryPolicy, evaluate_iv_history
from btc_option_scanner.analytics.regime import IVRegime, RegimePolicy, classify_iv_regime
from btc_option_scanner.domain import IVObservation, PriceBasis
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


def test_regime_is_history_gated_and_percentile_labelled() -> None:
    policy = IVHistoryPolicy("iv-window@1", 3, 3, Decimal("1"))
    target = observation("target", "0.90", 10)
    ready = evaluate_iv_history(
        target,
        (observation("a", "0.20", 1), observation("b", "0.40", 2), observation("c", "0.60", 3)),
        policy,
    )
    assessment = classify_iv_regime(ready, RegimePolicy("regime@1", Decimal("25"), Decimal("75")))
    assert assessment.regime is IVRegime.HIGH
    insufficient = evaluate_iv_history(target, (), policy)
    assert (
        classify_iv_regime(
            insufficient, RegimePolicy("regime@1", Decimal("25"), Decimal("75"))
        ).regime
        is IVRegime.INSUFFICIENT_HISTORY
    )
