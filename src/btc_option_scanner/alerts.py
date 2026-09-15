"""Live scanner presentation and optional Feishu bot notifications."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import AppConfig
from .data import SqliteRepository
from .domain import (
    Availability,
    DecisionState,
    InstrumentState,
    MarketSnapshot,
    OptionType,
    RunStatus,
    ScannerRun,
)
from .models.metrics import build_metric_bundle
from .scanner import Bucket, CohortPolicy, ScanInput, ScannerPolicy, scan_candidates

ALERT_SCHEMA_VERSION = "live-recommendation-v1"
ALERT_DISCLAIMER = "研究候选，仅供人工复核；不是交易指令，程序不会下单。"

# Stable fail-closed reason codes surfaced in status and the candidate file.
REASON_CACHE_ONLY = "CACHE_ONLY"
REASON_REFRESH_FAILED = "REFRESH_FAILED"
REASON_REFRESH_TIMEOUT = "REFRESH_TIMEOUT"
REASON_SNAPSHOT_SKEW = "SNAPSHOT_SKEW"
REASON_SNAPSHOT_STALE = "SNAPSHOT_STALE"
REASON_COHORT_SAMPLE_BELOW_POLICY = "COHORT_SAMPLE_BELOW_POLICY"
REASON_ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"

# Discovery-only prefilter bounds.  They are deliberately wider than the final
# Delta/spread gates so refreshed candidates are never dropped before the
# canonical scanner sees them; only the canonical policy produces candidates.
REFRESH_PREFILTER_DELTA_MARGIN = Decimal("0.05")
REFRESH_PREFILTER_SPREAD_MULTIPLIER = Decimal("1.5")
_MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000


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
    minimum_alert_interval_seconds: float = 1_800.0
    reminder_seconds: float = 21_600.0
    min_absolute_delta: Decimal = Decimal("0.80")
    max_absolute_delta: Decimal = Decimal("0.90")
    max_relative_spread: Decimal = Decimal("0.05")
    min_cohort_size: int = 3
    feishu_webhook_url: str | None = field(default=None, repr=False)
    feishu_signing_secret: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            self.interval_seconds <= 0
            or self.minimum_alert_interval_seconds <= 0
            or self.reminder_seconds < self.minimum_alert_interval_seconds
        ):
            raise ValueError("recommendation and reminder intervals must be positive")
        if self.top_n < 1 or self.top_n > 20:
            raise ValueError("recommendation top_n must be in [1, 20]")
        if self.min_cohort_size < 2:
            raise ValueError("recommendation min cohort size must be at least two")
        if (
            not self.min_absolute_delta.is_finite()
            or not self.max_absolute_delta.is_finite()
            or self.min_absolute_delta < 0
            or self.max_absolute_delta > 1
            or self.min_absolute_delta > self.max_absolute_delta
        ):
            raise ValueError("recommendation Delta bounds must satisfy 0 <= min <= max <= 1")
        if (
            not self.max_relative_spread.is_finite()
            or not Decimal("0") < self.max_relative_spread <= Decimal("1")
        ):
            raise ValueError("recommendation max relative spread must be in (0, 1]")
        if self.feishu_signing_secret is not None and self.feishu_webhook_url is None:
            raise ValueError("Feishu signing secret requires a configured webhook URL")
        if self.feishu_webhook_url is not None:
            parsed = urlparse(self.feishu_webhook_url)
            approved_hosts = {"open.feishu.cn", "open.larksuite.com"}
            if (
                parsed.scheme != "https"
                or parsed.hostname not in approved_hosts
                or parsed.username is not None
                or parsed.password is not None
                or not parsed.path.startswith("/open-apis/bot/v2/hook/")
            ):
                raise ValueError("Feishu webhook must be an approved HTTPS bot hook URL")

    @classmethod
    def from_environment(
        cls, environment: dict[str, str] | None = None
    ) -> LiveRecommendationSettings:
        source = dict(os.environ if environment is None else environment)
        try:
            interval = float(source.get("BTC_OPTION_RECOMMENDATION_INTERVAL_SECONDS", "60"))
            minimum_alert_interval = float(
                source.get("BTC_OPTION_ALERT_MIN_INTERVAL_SECONDS", "1800")
            )
            reminder = float(source.get("BTC_OPTION_ALERT_REMINDER_SECONDS", "21600"))
            top_n = int(source.get("BTC_OPTION_RECOMMENDATION_TOP_N", "3"))
            min_cohort_size = int(
                source.get("BTC_OPTION_RECOMMENDATION_MIN_COHORT_SIZE", "3")
            )
            min_delta = Decimal(source.get("BTC_OPTION_RECOMMENDATION_MIN_DELTA", "0.80"))
            max_delta = Decimal(source.get("BTC_OPTION_RECOMMENDATION_MAX_DELTA", "0.90"))
            max_spread = Decimal(
                source.get("BTC_OPTION_RECOMMENDATION_MAX_RELATIVE_SPREAD", "0.05")
            )
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("recommendation numeric environment value is invalid") from exc
        webhook_url = source.get("BTC_OPTION_FEISHU_WEBHOOK_URL", "").strip() or None
        signing_secret = source.get("BTC_OPTION_FEISHU_SIGNING_SECRET", "").strip() or None
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
            minimum_alert_interval_seconds=minimum_alert_interval,
            reminder_seconds=reminder,
            min_absolute_delta=min_delta,
            max_absolute_delta=max_delta,
            max_relative_spread=max_spread,
            min_cohort_size=min_cohort_size,
            feishu_webhook_url=webhook_url,
            feishu_signing_secret=signing_secret,
        )

    @property
    def feishu_enabled(self) -> bool:
        return self.feishu_webhook_url is not None


def select_coherent_snapshots_with_diagnostics(
    snapshots: tuple[MarketSnapshot, ...],
    *,
    as_of_utc: datetime,
    max_snapshot_skew_ms: int,
    max_age_seconds: float,
) -> tuple[tuple[MarketSnapshot, ...], dict[str, int]]:
    """Same-window rule as ``select_coherent_snapshots`` plus rejection counts.

    Adds an exchange-timestamp age gate on top of the observed-age gate, so an
    actively refreshed response carrying an old exchange timestamp is rejected
    as SNAPSHOT_STALE instead of silently entering a run.
    """
    if max_snapshot_skew_ms < 0 or max_age_seconds <= 0:
        raise ValueError("snapshot skew and age bounds are invalid")
    rejected: dict[str, int] = {
        "EXCHANGE_TIMESTAMP_ABSENT": 0,
        REASON_SNAPSHOT_SKEW: 0,
        REASON_SNAPSHOT_STALE: 0,
    }
    timestamps = [
        snapshot.source.exchange_timestamp_ms
        for snapshot in snapshots
        if snapshot.source.exchange_timestamp_ms is not None
    ]
    if not timestamps:
        return (), rejected
    newest = max(timestamps)
    selected: list[MarketSnapshot] = []
    as_of_ms = as_of_utc.timestamp() * 1000
    for snapshot in sorted(snapshots, key=lambda item: item.instrument_name):
        exchange_timestamp_ms = snapshot.source.exchange_timestamp_ms
        if exchange_timestamp_ms is None:
            rejected["EXCHANGE_TIMESTAMP_ABSENT"] += 1
            continue
        if newest - exchange_timestamp_ms > max_snapshot_skew_ms:
            rejected[REASON_SNAPSHOT_SKEW] += 1
            continue
        exchange_age_seconds = (as_of_ms - exchange_timestamp_ms) / 1000
        observed_age_seconds = (as_of_utc - snapshot.source.observed_at_utc).total_seconds()
        if exchange_age_seconds > max_age_seconds or observed_age_seconds > max_age_seconds:
            rejected[REASON_SNAPSHOT_STALE] += 1
            continue
        selected.append(snapshot)
    return tuple(selected), rejected


def build_feishu_payload(
    message: str,
    *,
    signing_secret: str | None = None,
    timestamp: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": message}}
    if signing_secret is not None:
        seconds = timestamp if timestamp is not None else int(datetime.now(UTC).timestamp())
        string_to_sign = f"{seconds}\n{signing_secret}".encode()
        signature = base64.b64encode(
            hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
        ).decode()
        payload.update({"timestamp": str(seconds), "sign": signature})
    return payload


def validate_feishu_response(response: dict[str, Any]) -> None:
    code = response.get("code", response.get("StatusCode"))
    if code != 0:
        message = response.get("msg", response.get("StatusMessage", "unknown error"))
        raise RuntimeError(f"Feishu webhook failed: code={code} message={message}")


def _post_feishu(settings: LiveRecommendationSettings, message: str) -> None:
    assert settings.feishu_webhook_url is not None
    payload = build_feishu_payload(message, signing_secret=settings.feishu_signing_secret)
    request = Request(
        settings.feishu_webhook_url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - validated host
            raw = response.read(1024 * 1024 + 1)
    except HTTPError as exc:
        raise RuntimeError(f"Feishu webhook returned HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError("Feishu webhook connection failed") from None
    if len(raw) > 1024 * 1024:
        raise RuntimeError("Feishu webhook response exceeds one MiB")
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise RuntimeError("Feishu webhook returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Feishu webhook returned a non-object response")
    validate_feishu_response(decoded)


async def send_feishu_message(settings: LiveRecommendationSettings, message: str) -> None:
    """Send one plain-text message through a Feishu custom-bot HTTPS webhook."""
    if not settings.feishu_enabled:
        return
    await asyncio.to_thread(_post_feishu, settings, message)


def _scanner_policy(
    config: AppConfig, recommendation_settings: LiveRecommendationSettings
) -> ScannerPolicy:
    quality_version = (
        "live-quality@1"
        f":delta={recommendation_settings.min_absolute_delta}"
        f"-{recommendation_settings.max_absolute_delta}"
        f":spread={recommendation_settings.max_relative_spread}"
        f":cohort={recommendation_settings.min_cohort_size}"
    )
    upper_dte = config.universe.max_dte_days + Decimal("0.000001")
    cohort = CohortPolicy(
        quality_version,
        (Bucket("delta-all", Decimal("0"), Decimal("1.000001")),),
        (
            Bucket(
                "configured-dte",
                config.universe.min_dte_days,
                upper_dte,
            ),
        ),
        recommendation_settings.min_cohort_size,
    )
    return ScannerPolicy(
        f"scanner-lexicographic@0.1.0-experimental+{quality_version}",
        quality_version,
        config.universe.min_dte_days,
        config.universe.max_dte_days,
        config.scanner.min_open_interest_base_btc,
        min(config.scanner.max_spread_fraction, recommendation_settings.max_relative_spread),
        cohort,
        config.market_data.max_snapshot_skew_ms,
        recommendation_settings.min_absolute_delta,
        recommendation_settings.max_absolute_delta,
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
    diagnostics: dict[str, Any] | None = None,
) -> None:
    """Replace any previous candidate file when live actionability is unavailable."""
    observed = generated_at_utc or datetime.now(UTC)
    payload = {
        "schema_version": ALERT_SCHEMA_VERSION,
        "generated_at_utc": observed.isoformat(),
        "scanner_run_id": None,
        "decision_state": state,
        "input_count": 0,
        "eligible_count": 0,
        "candidates": [],
        "unavailable_reason": reason,
        "disclaimer": ALERT_DISCLAIMER,
    }
    if diagnostics is not None:
        payload["diagnostics"] = diagnostics
    _atomic_json(settings.output_path, payload)


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    payload: dict[str, Any]
    notification_text: str | None
    scanner_run_id: str
    candidate_count: int
    top_fingerprint: str | None


def alert_delivery_due(
    *,
    now_monotonic: float,
    last_alert_monotonic: float | None,
    minimum_interval_seconds: float,
    reminder_seconds: float,
    fingerprint: str | None,
    last_fingerprint: str | None,
) -> bool:
    """Enforce a hard minimum interval even when the ranked set changes."""
    if last_alert_monotonic is None:
        return True
    elapsed = now_monotonic - last_alert_monotonic
    if elapsed < minimum_interval_seconds:
        return False
    return fingerprint != last_fingerprint or elapsed >= reminder_seconds


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


@dataclass(frozen=True, slots=True)
class RefreshPrefilterPolicy:
    """Versioned, deliberately wider discovery bounds used only to pick refresh targets."""

    version: str
    min_dte_days: Decimal
    max_dte_days: Decimal
    min_absolute_delta: Decimal
    max_absolute_delta: Decimal
    max_relative_spread: Decimal
    min_open_interest_base_btc: Decimal

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("prefilter policy version must not be empty")
        if self.min_dte_days < 0 or self.max_dte_days < self.min_dte_days:
            raise ValueError("prefilter DTE bounds are invalid")
        if self.min_absolute_delta < 0 or self.max_absolute_delta > 1:
            raise ValueError("prefilter Delta bounds must stay within [0, 1]")
        if not Decimal("0") < self.max_relative_spread <= Decimal("1"):
            raise ValueError("prefilter spread bound must be in (0, 1]")
        if self.min_open_interest_base_btc < 0:
            raise ValueError("prefilter open interest bound must be non-negative")


def build_refresh_prefilter_policy(
    config: AppConfig, settings: LiveRecommendationSettings
) -> RefreshPrefilterPolicy:
    """Derive the versioned prefilter from the unchanged final policy gates."""
    min_delta = max(
        Decimal("0"), settings.min_absolute_delta - REFRESH_PREFILTER_DELTA_MARGIN
    )
    max_delta = min(
        Decimal("1"), settings.max_absolute_delta + REFRESH_PREFILTER_DELTA_MARGIN
    )
    max_spread = min(
        Decimal("1"), settings.max_relative_spread * REFRESH_PREFILTER_SPREAD_MULTIPLIER
    )
    version = (
        "live-refresh-prefilter@1.0.0"
        f":dte={config.universe.min_dte_days}-{config.universe.max_dte_days}"
        f":delta={min_delta}-{max_delta}"
        f":spread<={max_spread}"
        f":oi>={config.scanner.min_open_interest_base_btc}"
    )
    return RefreshPrefilterPolicy(
        version,
        config.universe.min_dte_days,
        config.universe.max_dte_days,
        min_delta,
        max_delta,
        max_spread,
        config.scanner.min_open_interest_base_btc,
    )


def _passes_discovery_prefilter(
    snapshot: MarketSnapshot | None, policy: RefreshPrefilterPolicy
) -> bool:
    """Cheap cache-side proxy for the final policy; never produces candidates."""
    if snapshot is None or not snapshot.actionable:
        return False
    ask, bid = snapshot.ask, snapshot.bid
    for side in (ask, bid):
        if (
            side.price_btc_per_contract.availability is not Availability.AVAILABLE
            or side.price_btc_per_contract.value is None
            or side.amount_base_btc.availability is not Availability.AVAILABLE
            or side.amount_base_btc.value is None
        ):
            return False
    ask_price, bid_price = ask.price_btc_per_contract.value, bid.price_btc_per_contract.value
    assert ask_price is not None and bid_price is not None and bid_price <= ask_price
    mid = (ask_price + bid_price) / Decimal("2")
    relative_spread = (ask_price - bid_price) / mid
    if relative_spread > policy.max_relative_spread:
        return False
    delta = snapshot.greeks.delta_standard
    if delta.availability is not Availability.AVAILABLE or delta.value is None:
        return False
    if not policy.min_absolute_delta <= abs(delta.value) <= policy.max_absolute_delta:
        return False
    oi = snapshot.open_interest_amount_base_btc
    return (
        oi.availability is Availability.AVAILABLE
        and oi.value is not None
        and oi.value >= policy.min_open_interest_base_btc
    )


@dataclass(frozen=True, slots=True)
class RefreshPrefilterResult:
    """Discovery output: who looks policy-valid, and the deterministic probe pool."""

    decision_universe: tuple[str, ...]
    proxy_valid: tuple[str, ...]
    probe_pool: tuple[str, ...]


def prefilter_refresh_candidates(
    specs: Mapping[str, Any],
    cache: Mapping[str, MarketSnapshot],
    *,
    now_utc: datetime,
    policy: RefreshPrefilterPolicy,
) -> RefreshPrefilterResult:
    """Split subscribed decision-range calls into proxy-valid and probe pools.

    Only the subscribed universe is considered, and the DTE range is the same
    configured decision interval; collection-buffer instruments never enter.
    """
    now_ms = int(now_utc.timestamp() * 1000)
    decision: list[str] = []
    proxy: list[str] = []
    probe: list[str] = []
    for name, spec in sorted(specs.items()):
        if spec.option_type is not OptionType.CALL or spec.state is not InstrumentState.OPEN:
            continue
        dte_days = Decimal(spec.expiration_timestamp_ms - now_ms) / Decimal(_MILLISECONDS_PER_DAY)
        if not policy.min_dte_days <= dte_days <= policy.max_dte_days:
            continue
        decision.append(name)
        if _passes_discovery_prefilter(cache.get(name), policy):
            proxy.append(name)
        else:
            probe.append(name)
    return RefreshPrefilterResult(tuple(decision), tuple(proxy), tuple(probe))


def build_refresh_set(
    proxy_valid: tuple[str, ...],
    probe_pool: tuple[str, ...],
    *,
    limit: int,
    probe_offset: int,
) -> tuple[tuple[str, ...], int]:
    """Select the bounded per-cycle refresh set with a deterministic rotation.

    Proxy-valid instruments fill the set first; remaining slots rotate through
    the probe pool so temporarily quote-less or boundary-outside core contracts
    are re-probed instead of becoming a permanent blind spot.
    """
    if limit <= 0:
        raise ValueError("refresh limit must be positive")
    def rotating_slice(pool: tuple[str, ...], count: int) -> list[str]:
        if not pool or count <= 0:
            return []
        return [pool[(probe_offset + index) % len(pool)] for index in range(count)]

    # Keep a small rotating probe allocation even when cached proxy-valid names
    # exceed the limit. Rotate the proxy allocation as well, otherwise the
    # alphabetically first names could permanently monopolize every refresh.
    reserved_probe_slots = (
        min(len(probe_pool), max(1, limit // 6)) if probe_pool else 0
    )
    proxy_slots = min(len(proxy_valid), limit - reserved_probe_slots)
    names = rotating_slice(proxy_valid, proxy_slots)
    remaining = limit - len(names)
    rotation = min(remaining, len(probe_pool))
    names.extend(rotating_slice(probe_pool, rotation))
    return tuple(names), rotation


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
    policy = _scanner_policy(config, settings)
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
                "index_price_usd_per_btc": (
                    str(market.index_price_usd_per_btc.value)
                    if market.index_price_usd_per_btc.value is not None
                    else None
                ),
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
        "quality_policy": {
            "min_dte_days": str(config.universe.min_dte_days),
            "max_dte_days": str(config.universe.max_dte_days),
            "min_absolute_delta": str(settings.min_absolute_delta),
            "max_absolute_delta": str(settings.max_absolute_delta),
            "max_relative_spread": str(settings.max_relative_spread),
            "min_cohort_size": settings.min_cohort_size,
        },
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
            index_text = (
                f"${Decimal(item['index_price_usd_per_btc']):,.0f}"
                if item["index_price_usd_per_btc"] is not None
                else "unavailable"
            )
            lines.append(
                f"#{item['rank']} {item['instrument_name']}"
                f" | Ask {item['ask_btc_per_contract']} BTC"
                f" | Δ {item['delta']} | DTE {Decimal(item['dte_days']):.1f}"
                f" | Spread {Decimal(item['relative_spread']):.2%}"
                f" | Index {index_text}"
            )
        lines.append(ALERT_DISCLAIMER)
        text = "\n".join(lines)
    return RecommendationResult(payload, text, run_id, len(eligible), fingerprint)
