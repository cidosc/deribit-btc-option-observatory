import json
from dataclasses import replace
from decimal import Decimal

import pytest

from btc_option_scanner.alerts import (
    LiveRecommendationSettings,
    alert_delivery_due,
    build_feishu_payload,
    evaluate_live_recommendations,
    select_coherent_snapshots,
    validate_feishu_response,
    write_recommendation_unavailable,
)
from btc_option_scanner.config import load_config
from btc_option_scanner.data import SqliteRepository
from btc_option_scanner.domain import OptionType
from tests.models.factories import AS_OF, market_snapshot


def test_feishu_settings_are_optional_and_webhook_host_fails_closed() -> None:
    settings = LiveRecommendationSettings.from_environment({})
    assert settings.enabled is True
    assert settings.feishu_enabled is False
    assert settings.min_absolute_delta == Decimal("0.80")
    assert settings.max_absolute_delta == Decimal("0.90")
    assert settings.max_relative_spread == Decimal("0.05")
    assert settings.min_cohort_size == 3
    assert settings.minimum_alert_interval_seconds == 1_800
    assert settings.reminder_seconds == 21_600
    configured = LiveRecommendationSettings.from_environment(
        {
            "BTC_OPTION_FEISHU_WEBHOOK_URL": (
                "https://open.feishu.cn/open-apis/bot/v2/hook/test-hook-id"
            )
        }
    )
    assert configured.feishu_enabled is True
    assert "test-hook-id" not in repr(configured)
    with pytest.raises(ValueError, match="approved"):
        LiveRecommendationSettings.from_environment(
            {"BTC_OPTION_FEISHU_WEBHOOK_URL": "https://example.invalid/hook"}
        )
    with pytest.raises(ValueError, match="requires"):
        LiveRecommendationSettings.from_environment(
            {"BTC_OPTION_FEISHU_SIGNING_SECRET": "test-only-secret"}
        )


def test_feishu_webhook_payload_signing_and_response_contract() -> None:
    payload = build_feishu_payload(
        "candidate", signing_secret="test-secret", timestamp=1_700_000_000
    )
    assert payload["msg_type"] == "text"
    assert payload["content"] == {"text": "candidate"}
    assert payload["timestamp"] == "1700000000"
    assert payload["sign"] == "mbm4Y4oluIPQ00qlBIhX8vAZ0EKv3nw0LuTb91jPL84="
    validate_feishu_response({"code": 0, "msg": "success"})
    validate_feishu_response({"StatusCode": 0, "StatusMessage": "success"})
    with pytest.raises(RuntimeError, match="failed"):
        validate_feishu_response({"code": 19021, "msg": "sign match fail"})


def test_alert_minimum_interval_cannot_be_bypassed_by_candidate_changes() -> None:
    assert not alert_delivery_due(
        now_monotonic=1_100,
        last_alert_monotonic=1_000,
        minimum_interval_seconds=1_800,
        reminder_seconds=21_600,
        fingerprint="new-ranking",
        last_fingerprint="old-ranking",
    )
    assert alert_delivery_due(
        now_monotonic=2_800,
        last_alert_monotonic=1_000,
        minimum_interval_seconds=1_800,
        reminder_seconds=21_600,
        fingerprint="new-ranking",
        last_fingerprint="old-ranking",
    )
    assert not alert_delivery_due(
        now_monotonic=3_000,
        last_alert_monotonic=1_000,
        minimum_interval_seconds=1_800,
        reminder_seconds=21_600,
        fingerprint="same-ranking",
        last_fingerprint="same-ranking",
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
    settings = LiveRecommendationSettings(
        output_path=tmp_path / "latest.json", top_n=2, min_cohort_size=2
    )
    snapshots = (
        market_snapshot(
            name="BTC-A", ask=Decimal("0.80"), bid=Decimal("0.79"), delta=Decimal("0.85")
        ),
        market_snapshot(
            name="BTC-B", ask=Decimal("0.75"), bid=Decimal("0.74"), delta=Decimal("0.85")
        ),
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
    assert payload["quality_policy"]["min_dte_days"] == "90"
    assert payload["quality_policy"]["max_dte_days"] == "200"
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
