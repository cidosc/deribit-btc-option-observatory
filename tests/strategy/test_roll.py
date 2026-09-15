from datetime import timedelta
from decimal import Decimal

from models.factories import AS_OF, market_snapshot

from btc_option_scanner.domain import (
    DecisionState,
    InstrumentSpec,
    InstrumentState,
    OptionType,
    QuantityMode,
    ValueState,
)
from btc_option_scanner.models import build_metric_bundle
from btc_option_scanner.strategy import (
    AgingPolicy,
    FeeSchedule,
    RollGatePolicy,
    RollInput,
    RoundingMode,
    RoundingPolicy,
    evaluate_roll,
    round_contract_quantity,
)


def _spec(name: str) -> InstrumentSpec:
    return InstrumentSpec(
        name,
        OptionType.CALL,
        InstrumentState.OPEN,
        Decimal("20000"),
        1798185600000,
        Decimal("1"),
        Decimal("0.1"),
        Decimal("0.0001"),
        "spec@1",
        f"raw-{name}",
    )


def _input(**overrides: object) -> RollInput:
    old = market_snapshot(
        name="BTC-25DEC26-20000-C", bid=Decimal("0.7"), ask=Decimal("0.71"), delta=Decimal("1")
    )
    new = market_snapshot(
        name="BTC-25DEC26-50000-C", bid=Decimal("0.29"), ask=Decimal("0.3"), delta=Decimal("0.8")
    )
    defaults: dict[str, object] = {
        "portfolio_snapshot_id": "portfolio-snapshot-1",
        "reserve_before_btc": Decimal("1"),
        "old_quantity_contracts": Decimal("2"),
        "old_market": old,
        "new_market": new,
        "old_metrics": build_metric_bundle(old, as_of_utc=AS_OF, config_version="test").core,
        "new_metrics": build_metric_bundle(new, as_of_utc=AS_OF, config_version="test").core,
        "old_spec": _spec(old.instrument_name),
        "new_spec": _spec(new.instrument_name),
        "quantity_mode": QuantityMode.EQUAL_CONTRACTS,
        "fee_schedule": FeeSchedule("fee@1", Decimal("0.01"), "test-evidence", True),
        "rounding_policy": RoundingPolicy("round@1", RoundingMode.NEAREST_HALF_UP, Decimal("0.1")),
        "aging_policy": AgingPolicy("aging@1", Decimal("110"), Decimal("0.95")),
        "gate_policy": RollGatePolicy(
            "gates@1",
            1000,
            timedelta(days=7),
            Decimal("0.1"),
            Decimal("0.05"),
            Decimal("0.1"),
            Decimal("1"),
            Decimal("1"),
            Decimal("3"),
        ),
        "as_of_utc": AS_OF,
        "config_version": "test",
        "utility_improvement": ValueState.available(Decimal("0.2")),
    }
    defaults.update(overrides)
    return RollInput(**defaults)  # type: ignore[arg-type]


def test_roll_cashflow_is_conserved_and_release_stays_reserve() -> None:
    output = evaluate_roll(_input())

    assert output.evaluation.decision_state is DecisionState.ROLL_CANDIDATE
    assert output.cashflow.old_sale_proceeds_btc.value == Decimal("1.4")
    assert output.cashflow.new_purchase_cost_btc.value == Decimal("0.6")
    assert output.cashflow.fee_old_btc.value == Decimal("0.014")
    assert output.cashflow.fee_new_btc.value == Decimal("0.006")
    assert output.cashflow.net_reserve_release_btc.value == Decimal("0.780")
    assert output.cashflow.reserve_after_btc.value == Decimal("1.780")
    assert output.cashflow.accounting_identity_holds.value is True


def test_equal_delta_reports_theoretical_and_rounded_quantity() -> None:
    output = evaluate_roll(_input(quantity_mode=QuantityMode.EQUAL_STANDARD_DELTA))

    assert output.evaluation.new_quantity_theoretical_contracts == Decimal("2.5")
    assert output.evaluation.new_quantity_executable_contracts.value == Decimal("2.5")
    assert output.evaluation.standard_delta_after_executable.value == Decimal("2.00")
    assert output.standard_delta_rounding_error.value == Decimal("0.00")
    assert (
        round_contract_quantity(
            Decimal("0.04"),
            min_trade_quantity_contracts=Decimal("0.1"),
            policy=RoundingPolicy("floor@1", RoundingMode.FLOOR, Decimal("0.1")),
        )
        == 0
    )


def test_missing_quotes_stale_interval_and_improvement_fail_closed() -> None:
    base = _input()
    assert (
        evaluate_roll(_input(old_market=market_snapshot(bid=None))).evaluation.decision_state
        is DecisionState.NO_EXECUTABLE_BID
    )
    assert (
        evaluate_roll(
            _input(new_market=market_snapshot(name="BTC-25DEC26-50000-C", ask=None))
        ).evaluation.decision_state
        is DecisionState.NO_EXECUTABLE_ASK
    )
    stale_new = market_snapshot(name="BTC-25DEC26-50000-C", stale=True)
    assert (
        evaluate_roll(_input(new_market=stale_new)).evaluation.decision_state
        is DecisionState.DATA_STALE
    )
    assert (
        evaluate_roll(_input(last_roll_at_utc=AS_OF - timedelta(days=1))).evaluation.decision_state
        is DecisionState.HOLD
    )
    assert (
        evaluate_roll(
            _input(utility_improvement=ValueState.available(Decimal("0")))
        ).evaluation.decision_state
        is DecisionState.HOLD
    )
    assert base.old_quantity_contracts == Decimal("2")
