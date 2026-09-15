import json
from dataclasses import replace
from decimal import Decimal

import pytest

from btc_option_scanner.alerts import (
    LiveRecommendationSettings,
    build_onebot_request,
    evaluate_live_recommendations,
    select_coherent_snapshots,
    validate_onebot_response,
    write_recommendation_unavailable,
)
from btc_option_scanner.config import load_config
from btc_option_scanner.data import SqliteRepository
from btc_option_scanner.domain import OptionType
from tests.models.factories import AS_OF, market_snapshot


def test_onebot_settings_are_optional_and_partial_configuration_fails_closed() -> None:
    settings = LiveRecommendationSettings.from_environment({})
    assert settings.enabled is True
    assert settings.onebot_enabled is False
    with pytest.raises(ValueError, match="configured together"):
        LiveRecommendationSettings.from_environment(
            {"BTC_OPTION_ONEBOT_WS_URL": "ws://127.0.0.1:3001/api"}
        )
    with pytest.raises(ValueError, match="requires"):
        LiveRecommendationSettings.from_environment(
            {"BTC_OPTION_ONEBOT_ACCESS_TOKEN": "test-only-token"}
        )


def test_onebot_v11_request_and_response_contract() -> None:
    request = build_onebot_request(
        target_type="group", target_id=123, message="candidate", echo="echo-1"
    )
    assert request == {
        "action": "send_group_msg",
        "params": {"group_id": 123, "message": "candidate", "auto_escape": True},
        "echo": "echo-1",
    }
    validate_onebot_response(
        {"status": "ok", "retcode": 0, "echo": "echo-1"}, expected_echo="echo-1"
    )
    with pytest.raises(RuntimeError, match="failed"):
        validate_onebot_response(
            {"status": "failed", "retcode": 100, "echo": "echo-1"},
            expected_echo="echo-1",
        )


def test_coherent_snapshot_selection_excludes_old_exchange_windows() -> None:
    snapshots = (
        market_snapshot(name="BTC-A", exchange_timestamp_ms=1_000),
        market_snapshot(name="BTC-B", exchange_timestamp_ms=2_999),
        market_snapshot(name="BTC-C", exchange_timestamp_ms=3_001),
    )
    selected = select_coherent_snapshots(
        snapshots,
        as_of_utc=AS_OF,
        max_snapshot_skew_ms=2_000,
        max_age_seconds=30,
    )
    assert [snapshot.instrument_name for snapshot in selected] == ["BTC-B", "BTC-C"]


def test_unavailable_state_replaces_previous_candidates(tmp_path) -> None:
    settings = LiveRecommendationSettings(output_path=tmp_path / "latest.json")
    settings.output_path.write_text('{"candidates":[{"old":true}]}', encoding="utf-8")
    write_recommendation_unavailable(
        settings,
        state="DATA_STALE",
        reason="DISCONNECTED",
        generated_at_utc=AS_OF,
    )
    payload = json.loads(settings.output_path.read_text(encoding="utf-8"))
    assert payload["candidates"] == []
    assert payload["decision_state"] == "DATA_STALE"
    assert payload["unavailable_reason"] == "DISCONNECTED"


def test_live_recommendation_reuses_scanner_and_persists_lineage(tmp_path) -> None:
    config = load_config("config.yaml")
    config = replace(
        config,
        universe=replace(
            config.universe,
            min_dte_days=Decimal("90"),
            max_dte_days=Decimal("200"),
        ),
        scanner=replace(config.scanner, min_cohort_size=2),
    )
    repository = SqliteRepository(tmp_path / "recommendations.sqlite")
    settings = LiveRecommendationSettings(output_path=tmp_path / "latest.json", top_n=2)
    snapshots = (
        market_snapshot(name="BTC-A", ask=Decimal("0.80")),
        market_snapshot(name="BTC-B", ask=Decimal("0.75")),
    )
    result = evaluate_live_recommendations(
        snapshots,
        {"BTC-A": OptionType.CALL, "BTC-B": OptionType.CALL},
        config=config,
        repository=repository,
        settings=settings,
        as_of_utc=AS_OF,
    )
    payload = json.loads(settings.output_path.read_text(encoding="utf-8"))
    assert result.candidate_count == 2
    assert payload["candidates"][0]["instrument_name"] == "BTC-B"
    assert payload["candidates"][0]["ask_btc_per_contract"] == "0.75"
    assert "不是交易指令" in payload["disclaimer"]
    assert len(repository.derived_entity_rows("MetricSnapshot")) == 2
    runs = repository.derived_entity_rows("ScannerRun")
    assert len(runs) == 1
    assert set(runs[0].parent_ids) == {
        "mkt-BTC-A",
        "mkt-BTC-B",
        *(row.entity_id for row in repository.derived_entity_rows("MetricSnapshot")),
    }
    repository.close()
