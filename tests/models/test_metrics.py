from decimal import Decimal

from btc_option_scanner.domain import Availability
from btc_option_scanner.models.metrics import QUANT_METRIC_VERSION, build_metric_bundle

from .factories import AS_OF, market_snapshot


def test_side_metrics_are_separate_and_ask_dce_cannot_rise_when_ask_rises() -> None:
    first = build_metric_bundle(
        market_snapshot(ask=Decimal("0.70")), as_of_utc=AS_OF, config_version="cfg@1"
    )
    second = build_metric_bundle(
        market_snapshot(ask=Decimal("0.80")), as_of_utc=AS_OF, config_version="cfg@1"
    )
    assert first.core.entry_ask.dce.value > second.core.entry_ask.dce.value
    assert first.core.liquidation_bid.dce.value != first.core.entry_ask.dce.value
    assert first.core.mark.dce.value is not None
    assert first.quantitative.metric_version == QUANT_METRIC_VERSION
    assert first.quantitative.gce_entry_ask.delta_response.availability is Availability.UNAVAILABLE


def test_no_executable_ask_has_no_entry_metric_and_metrics_are_deterministic() -> None:
    snapshot = market_snapshot(ask=None)
    one = build_metric_bundle(snapshot, as_of_utc=AS_OF, config_version="cfg@same")
    two = build_metric_bundle(snapshot, as_of_utc=AS_OF, config_version="cfg@same")
    assert one.core.entry_ask.dce.availability is Availability.UNAVAILABLE
    assert one.core.metric_snapshot_id == two.core.metric_snapshot_id
    assert one.quantitative.moneyness_index_over_strike.value > 1
    assert one.core.dte_days > 0
