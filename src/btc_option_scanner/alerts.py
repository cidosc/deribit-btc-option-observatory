"""Live scanner presentation and optional OneBot v11 WebSocket notifications."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import AppConfig
from .data import SqliteRepository
from .domain import DecisionState, MarketSnapshot, OptionType, RunStatus, ScannerRun
from .models.metrics import build_metric_bundle
from .scanner import Bucket, CohortPolicy, ScanInput, ScannerPolicy, scan_candidates

ALERT_SCHEMA_VERSION = "live-recommendation-v1"
ALERT_DISCLAIMER = "研究候选，仅供人工复核；不是交易指令，程序不会下单。"


def _env_bool(environment: dict[str, str], name: str, default: bool) -> bool:
    value = environment.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class LiveRecommendationSettings:
    enabled: bool = True
    output_path: Path = Path("runtime/latest-recommendation.json")
    interval_seconds: float = 60.0
    top_n: int = 3
    reminder_seconds: float = 3_600.0
    onebot_ws_url: str | None = None
    onebot_target_type: str | None = None
    onebot_target_id: int | None = None
    onebot_access_token: str | None = None

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0 or self.reminder_seconds <= 0:
            raise ValueError("recommendation and reminder intervals must be positive")
        if self.top_n < 1 or self.top_n > 20:
            raise ValueError("recommendation top_n must be in [1, 20]")
        configured = (
            self.onebot_ws_url is not None,
            self.onebot_target_type is not None,
            self.onebot_target_id is not None,
        )
        if any(configured) and not all(configured):
            raise ValueError("OneBot URL, target type, and target ID must be configured together")
        if self.onebot_access_token is not None and self.onebot_ws_url is None:
            raise ValueError("OneBot access token requires a configured WebSocket URL")
        if self.onebot_target_type not in {None, "private", "group"}:
            raise ValueError("OneBot target type must be private or group")
        if self.onebot_target_id is not None and self.onebot_target_id <= 0:
            raise ValueError("OneBot target ID must be positive")
        if self.onebot_ws_url is not None and not self.onebot_ws_url.startswith(("ws://", "wss://")):
            raise ValueError("OneBot URL must use ws:// or wss://")

    @classmethod
    def from_environment(
        cls, environment: dict[str, str] | None = None
    ) -> LiveRecommendationSettings:
        source = dict(os.environ if environment is None else environment)
        raw_target = source.get("BTC_OPTION_ONEBOT_TARGET_ID", "").strip()
        try:
            target_id = int(raw_target) if raw_target else None
            interval = float(source.get("BTC_OPTION_RECOMMENDATION_INTERVAL_SECONDS", "60"))
            reminder = float(source.get("BTC_OPTION_ALERT_REMINDER_SECONDS", "3600"))
            top_n = int(source.get("BTC_OPTION_RECOMMENDATION_TOP_N", "3"))
        except ValueError as exc:
            raise ValueError("recommendation numeric environment value is invalid") from exc
        url = source.get("BTC_OPTION_ONEBOT_WS_URL", "").strip() or None
        target_type = source.get("BTC_OPTION_ONEBOT_TARGET_TYPE", "").strip() or None
        token = source.get("BTC_OPTION_ONEBOT_ACCESS_TOKEN", "").strip() or None
        return cls(
            enabled=_env_bool(source, "BTC_OPTION_RECOMMENDATIONS_ENABLED", True),
            output_path=Path(
                source.get(
                    "BTC_OPTION_RECOMMENDATION_PATH",
                    "runtime/latest-recommendation.json",
                )
            ),
            interval_seconds=interval,
            top_n=top_n,
            reminder_seconds=reminder,
            onebot_ws_url=url,
            onebot_target_type=target_type,
            onebot_target_id=target_id,
            onebot_access_token=token,
        )

    @property
    def onebot_enabled(self) -> bool:
        return self.onebot_ws_url is not None


def build_onebot_request(
    *, target_type: str, target_id: int, message: str, echo: str
) -> dict[str, Any]:
    if target_type not in {"private", "group"}:
        raise ValueError("OneBot target type must be private or group")
    id_field = "user_id" if target_type == "private" else "group_id"
    return {
        "action": f"send_{target_type}_msg",
        "params": {id_field: target_id, "message": message, "auto_escape": True},
        "echo": echo,
    }


def validate_onebot_response(response: dict[str, Any], *, expected_echo: str) -> None:
    if response.get("echo") != expected_echo:
        raise RuntimeError("OneBot response echo does not match the request")
    if response.get("status") != "ok" or response.get("retcode") != 0:
        raise RuntimeError(
            f"OneBot action failed: status={response.get('status')} "
            f"retcode={response.get('retcode')}"
        )


async def send_onebot_message(settings: LiveRecommendationSettings, message: str) -> None:
    """Send one plain-text action over a OneBot v11 forward WebSocket API."""
    if not settings.onebot_enabled:
        return
    assert settings.onebot_ws_url is not None
    assert settings.onebot_target_type is not None
    assert settings.onebot_target_id is not None
    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:
        raise RuntimeError("install collector dependencies to use OneBot WebSocket") from exc
    echo = str(uuid.uuid4())
    request = build_onebot_request(
        target_type=settings.onebot_target_type,
        target_id=settings.onebot_target_id,
        message=message,
        echo=echo,
    )
    headers = (
        {"Authorization": f"Bearer {settings.onebot_access_token}"}
        if settings.onebot_access_token
        else None
    )
    async with connect(
        settings.onebot_ws_url,
        additional_headers=headers,
        open_timeout=10,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
        max_size=1024 * 1024,
    ) as websocket:
        await websocket.send(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
        async with asyncio.timeout(10):
            while True:
                raw = await websocket.recv()
                try:
                    response = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
                    raise RuntimeError("OneBot returned invalid JSON") from exc
                if isinstance(response, dict) and response.get("echo") == echo:
                    break
    validate_onebot_response(response, expected_echo=echo)


def _scanner_policy(config: AppConfig) -> ScannerPolicy:
    upper_dte = config.universe.max_dte_days + Decimal("0.000001")
    cohort = CohortPolicy(
        "live-broad-cohort@1",
        (Bucket("delta-all", Decimal("0"), Decimal("1.000001")),),
        (
            Bucket(
                "configured-dte",
                config.universe.min_dte_days,
                upper_dte,
            ),
        ),
        config.scanner.min_cohort_size,
    )
    return ScannerPolicy(
        "scanner-lexicographic@0.1.0-experimental",
        "live-collection-universe@1",
        config.universe.min_dte_days,
        config.universe.max_dte_days,
        config.scanner.min_open_interest_base_btc,
        config.scanner.max_spread_fraction,
        cohort,
        config.market_data.max_snapshot_skew_ms,
    )


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_recommendation_unavailable(
    settings: LiveRecommendationSettings,
    *,
    state: str,
    reason: str,
    generated_at_utc: datetime | None = None,
) -> None:
    """Replace any previous candidate file when live actionability is unavailable."""
    observed = generated_at_utc or datetime.now(UTC)
    _atomic_json(
        settings.output_path,
        {
            "schema_version": ALERT_SCHEMA_VERSION,
            "generated_at_utc": observed.isoformat(),
            "scanner_run_id": None,
            "decision_state": state,
            "input_count": 0,
            "eligible_count": 0,
            "candidates": [],
            "unavailable_reason": reason,
            "disclaimer": ALERT_DISCLAIMER,
        },
    )


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    payload: dict[str, Any]
    notification_text: str | None
    scanner_run_id: str
    candidate_count: int
    top_fingerprint: str | None


def select_coherent_snapshots(
    snapshots: tuple[MarketSnapshot, ...],
    *,
    as_of_utc: datetime,
    max_snapshot_skew_ms: int,
    max_age_seconds: float,
) -> tuple[MarketSnapshot, ...]:
    """Select one fresh exchange-time window without weakening scanner gates."""
    if max_snapshot_skew_ms < 0 or max_age_seconds <= 0:
        raise ValueError("snapshot skew and age bounds are invalid")
    timestamps = [
        snapshot.source.exchange_timestamp_ms
        for snapshot in snapshots
        if snapshot.source.exchange_timestamp_ms is not None
    ]
    if not timestamps:
        return ()
    newest = max(timestamps)
    return tuple(
        sorted(
            (
                snapshot
                for snapshot in snapshots
                if snapshot.source.exchange_timestamp_ms is not None
                and newest - snapshot.source.exchange_timestamp_ms <= max_snapshot_skew_ms
                and (as_of_utc - snapshot.source.observed_at_utc).total_seconds()
                <= max_age_seconds
            ),
            key=lambda snapshot: snapshot.instrument_name,
        )
    )


def evaluate_live_recommendations(
    snapshots: tuple[MarketSnapshot, ...],
    option_types: dict[str, OptionType],
    *,
    config: AppConfig,
    repository: SqliteRepository,
    settings: LiveRecommendationSettings,
    as_of_utc: datetime,
) -> RecommendationResult:
    """Run the existing scanner and persist its exact metric/input lineage."""
    if as_of_utc.tzinfo is None or as_of_utc.utcoffset() != UTC.utcoffset(as_of_utc):
        raise ValueError("recommendation as_of_utc must be aware UTC")
    policy = _scanner_policy(config)
    bundles = [
        build_metric_bundle(snapshot, as_of_utc=as_of_utc, config_version=config.config_version)
        for snapshot in snapshots
    ]
    for bundle in bundles:
        repository.save_metric(bundle.core)
    inputs = tuple(
        ScanInput(option_types[snapshot.instrument_name], snapshot, bundle.core)
        for snapshot, bundle in zip(snapshots, bundles, strict=True)
    )
    evaluations = scan_candidates(inputs, policy)
    eligible = sorted(
        (evaluation for evaluation in evaluations if evaluation.eligible),
        key=lambda evaluation: evaluation.rank or 10**9,
    )
    decision = DecisionState.ELIGIBLE if eligible else DecisionState.DATA_ONLY
    run_id = "scanner:" + hashlib.sha256(
        (as_of_utc.isoformat() + "|" + "|".join(x.market_snapshot_id for x in snapshots)).encode()
    ).hexdigest()
    run = ScannerRun(
        run_id,
        as_of_utc,
        tuple(snapshot.market_snapshot_id for snapshot in snapshots),
        tuple(bundle.core.metric_snapshot_id for bundle in bundles),
        policy.universe_filter_version,
        config.config_version,
        policy.max_snapshot_skew_ms,
        RunStatus.COMPLETED if eligible else RunStatus.COMPLETED_DATA_ONLY,
        decision,
        evaluations,
    )
    repository.save_scanner_run(run)
    market_by_name = {snapshot.instrument_name: snapshot for snapshot in snapshots}
    metric_by_name = {
        snapshot.instrument_name: bundle.core
        for snapshot, bundle in zip(snapshots, bundles, strict=True)
    }
    candidates = []
    for evaluation in eligible[: settings.top_n]:
        market = market_by_name[evaluation.instrument_name]
        metric = metric_by_name[evaluation.instrument_name]
        candidates.append(
            {
                "instrument_name": evaluation.instrument_name,
                "rank": evaluation.rank,
                "ask_btc_per_contract": str(market.ask.price_btc_per_contract.value),
                "ask_amount_base_btc": str(market.ask.amount_base_btc.value),
                "ask_quantity_contracts": str(market.best_ask_quantity_contracts.value),
                "delta": str(market.greeks.delta_standard.value),
                "dte_days": str(metric.dte_days),
                "entry_dce": str(metric.entry_ask.dce.value),
                "relative_spread": str(metric.relative_spread.value),
                "mark_iv_decimal": (
                    str(market.iv.mark_annual_decimal.value)
                    if market.iv.mark_annual_decimal.value is not None
                    else None
                ),
                "market_snapshot_id": market.market_snapshot_id,
                "metric_snapshot_id": metric.metric_snapshot_id,
                "reasons": list(evaluation.reasons),
                "risks": list(evaluation.risks),
            }
        )
    payload = {
        "schema_version": ALERT_SCHEMA_VERSION,
        "generated_at_utc": as_of_utc.isoformat(),
        "scanner_run_id": run_id,
        "decision_state": decision.value,
        "input_count": len(snapshots),
        "eligible_count": len(eligible),
        "candidates": candidates,
        "disclaimer": ALERT_DISCLAIMER,
    }
    _atomic_json(settings.output_path, payload)
    fingerprint = None
    text = None
    if candidates:
        # Quote changes are frequent; notify on a changed ranked set and use the
        # configured reminder for unchanged candidates instead of spamming every scan.
        fingerprint_material = "|".join(str(item["instrument_name"]) for item in candidates)
        fingerprint = hashlib.sha256(fingerprint_material.encode()).hexdigest()
        lines = ["Deribit BTC Call 实时研究候选"]
        for item in candidates:
            lines.append(
                f"#{item['rank']} {item['instrument_name']}"
                f" | Ask {item['ask_btc_per_contract']} BTC"
                f" | Δ {item['delta']} | DTE {Decimal(item['dte_days']):.1f}"
                f" | Spread {Decimal(item['relative_spread']):.2%}"
            )
        lines.append(ALERT_DISCLAIMER)
        text = "\n".join(lines)
    return RecommendationResult(payload, text, run_id, len(eligible), fingerprint)


Notifier = Callable[[LiveRecommendationSettings, str], Awaitable[None]]
