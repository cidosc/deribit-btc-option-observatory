"""Long-running, read-only Deribit market-data collector for Linux services."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import shutil
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .alerts import (
    REASON_CACHE_ONLY,
    REASON_COHORT_SAMPLE_BELOW_POLICY,
    REASON_ENVIRONMENT_MISMATCH,
    REASON_REFRESH_FAILED,
    REASON_REFRESH_TIMEOUT,
    REASON_SNAPSHOT_SKEW,
    REASON_SNAPSHOT_STALE,
    LiveRecommendationSettings,
    alert_delivery_due,
    build_refresh_prefilter_policy,
    build_refresh_set,
    evaluate_live_recommendations,
    prefilter_refresh_candidates,
    select_coherent_snapshots_with_diagnostics,
    send_feishu_message,
    write_recommendation_unavailable,
)
from .config import load_config
from .data import SqliteRepository
from .deribit import (
    AuthenticationError,
    BookSequenceTracker,
    BookSubscription,
    DeribitCredentials,
    DeribitPublicRestClient,
    NormalizationError,
    PublicDataSpine,
    RateLimitConfig,
    ReadOnlyWebSocketAuthenticator,
    ReconnectingTickerStream,
    TickerSubscription,
    connect_public_websocket,
    rest_base_url_for_websocket,
)
from .deribit.parser import record_raw_event
from .domain import DecisionState, InstrumentState, OptionType, SourceTransport, SystemEvent

LOGGER = logging.getLogger("btc_option_scanner.collector")
APPROVED_WS_URLS = {
    "wss://www.deribit.com/ws/api/v2",
    "wss://test.deribit.com/ws/api/v2",
}


def _env_int(environment: dict[str, str], name: str, default: int) -> int:
    value = environment.get(name)
    try:
        return default if value is None or not value.strip() else int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(environment: dict[str, str], name: str, default: float) -> float:
    value = environment.get(name)
    try:
        return default if value is None or not value.strip() else float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


@dataclass(frozen=True, slots=True)
class CollectorSettings:
    database_path: Path = Path("data/collector.sqlite")
    status_path: Path = Path("runtime/collector-status.json")
    config_path: Path = Path("config.yaml")
    websocket_url: str = "wss://www.deribit.com/ws/api/v2"
    ticker_interval: str = "agg2"
    book_interval: str = "100ms"
    collection_profile: str = "scanner-lite"
    max_instruments: int = 60
    max_book_instruments: int = 0
    ticker_persist_interval_seconds: float = 60.0
    collection_buffer_days: float = 30.0
    refresh_limit: int = 12
    refresh_requests_per_second: float = 20.0
    refresh_timeout_seconds: float = 5.0
    refresh_cycle_budget_seconds: float = 6.0
    refresh_max_concurrency: int = 12
    catalog_refresh_seconds: float = 3_600.0
    retention_days: float = 30.0
    retention_check_seconds: float = 3_600.0
    status_interval_seconds: float = 30.0
    minimum_free_bytes: int = 2_147_483_648
    log_level: str = "INFO"
    log_file: Path | None = None
    explicit_instruments: tuple[str, ...] = ()
    auth_scope: str = "connection account:none trade:none wallet:none"

    def __post_init__(self) -> None:
        if self.websocket_url not in APPROVED_WS_URLS:
            raise ValueError("collector WebSocket URL must be an approved Deribit endpoint")
        if self.ticker_interval not in {"agg2", "100ms"}:
            raise ValueError("ticker interval must be agg2 or 100ms")
        if self.book_interval not in {"agg2", "100ms", "raw"}:
            raise ValueError("book interval must be agg2, 100ms, or raw")
        if self.collection_profile not in {"scanner-lite", "evidence"}:
            raise ValueError("collection profile must be scanner-lite or evidence")
        if self.max_instruments < 1 or self.max_book_instruments < 0:
            raise ValueError("instrument limits are outside their allowed range")
        if self.max_book_instruments > self.max_instruments:
            raise ValueError("max book instruments cannot exceed max instruments")
        if self.max_instruments + self.max_book_instruments + 1 > 500:
            raise ValueError("configured subscriptions exceed Deribit's 500-channel limit")
        positive = {
            "catalog_refresh_seconds": self.catalog_refresh_seconds,
            "retention_days": self.retention_days,
            "retention_check_seconds": self.retention_check_seconds,
            "status_interval_seconds": self.status_interval_seconds,
        }
        if any(value <= 0 for value in positive.values()):
            raise ValueError("collector intervals and retention days must be positive")
        if (
            self.collection_buffer_days < 0
            or self.ticker_persist_interval_seconds < 0
            or self.minimum_free_bytes < 1
        ):
            raise ValueError("collection buffer and minimum free bytes are invalid")
        if self.refresh_limit < 1 or self.refresh_limit > self.max_instruments:
            raise ValueError("refresh limit must be within [1, max instruments]")
        if self.refresh_requests_per_second <= 0:
            raise ValueError("refresh requests per second must be positive")
        if self.refresh_timeout_seconds <= 0 or self.refresh_cycle_budget_seconds <= 0:
            raise ValueError("refresh timeout and cycle budget must be positive")
        if self.refresh_max_concurrency < 1:
            raise ValueError("refresh max concurrency must be at least one")
        if self.log_level.upper() not in logging.getLevelNamesMapping():
            raise ValueError("unknown log level")

    @classmethod
    def from_environment(
        cls, environment: dict[str, str] | None = None
    ) -> CollectorSettings:
        source = dict(os.environ if environment is None else environment)
        profile = source.get("BTC_OPTION_COLLECTION_PROFILE", "scanner-lite").strip()
        profile_defaults = {
            "scanner-lite": (60, 0, 60.0),
            "evidence": (180, 20, 0.0),
        }
        if profile not in profile_defaults:
            raise ValueError("BTC_OPTION_COLLECTION_PROFILE must be scanner-lite or evidence")
        default_instruments, default_books, default_persist_interval = profile_defaults[profile]
        names = tuple(
            item.strip()
            for item in source.get("BTC_OPTION_INSTRUMENTS", "").split(",")
            if item.strip()
        )
        log_file = source.get("BTC_OPTION_LOG_FILE", "").strip()
        max_instruments = _env_int(source, "BTC_OPTION_MAX_INSTRUMENTS", default_instruments)
        default_refresh_limit = min(12, max_instruments)
        return cls(
            database_path=Path(source.get("BTC_OPTION_DB_PATH", "data/collector.sqlite")),
            status_path=Path(
                source.get("BTC_OPTION_STATUS_PATH", "runtime/collector-status.json")
            ),
            config_path=Path(source.get("BTC_OPTION_SCANNER_CONFIG", "config.yaml")),
            websocket_url=source.get(
                "BTC_OPTION_WS_URL", "wss://www.deribit.com/ws/api/v2"
            ),
            ticker_interval=source.get("BTC_OPTION_TICKER_INTERVAL", "agg2"),
            book_interval=source.get("BTC_OPTION_BOOK_INTERVAL", "100ms"),
            collection_profile=profile,
            max_instruments=max_instruments,
            max_book_instruments=_env_int(
                source, "BTC_OPTION_MAX_BOOK_INSTRUMENTS", default_books
            ),
            ticker_persist_interval_seconds=_env_float(
                source,
                "BTC_OPTION_TICKER_PERSIST_INTERVAL_SECONDS",
                default_persist_interval,
            ),
            collection_buffer_days=_env_float(
                source, "BTC_OPTION_COLLECTION_BUFFER_DAYS", 30.0
            ),
            refresh_limit=_env_int(source, "BTC_OPTION_REFRESH_LIMIT", default_refresh_limit),
            refresh_requests_per_second=_env_float(
                source, "BTC_OPTION_REFRESH_REQUESTS_PER_SECOND", 20.0
            ),
            refresh_timeout_seconds=_env_float(
                source, "BTC_OPTION_REFRESH_TIMEOUT_SECONDS", 5.0
            ),
            refresh_cycle_budget_seconds=_env_float(
                source, "BTC_OPTION_REFRESH_CYCLE_BUDGET_SECONDS", 6.0
            ),
            refresh_max_concurrency=_env_int(
                source, "BTC_OPTION_REFRESH_MAX_CONCURRENCY", 12
            ),
            catalog_refresh_seconds=_env_float(
                source, "BTC_OPTION_CATALOG_REFRESH_SECONDS", 3_600.0
            ),
            retention_days=_env_float(source, "BTC_OPTION_RETENTION_DAYS", 30.0),
            retention_check_seconds=_env_float(
                source, "BTC_OPTION_RETENTION_CHECK_SECONDS", 3_600.0
            ),
            status_interval_seconds=_env_float(
                source, "BTC_OPTION_STATUS_INTERVAL_SECONDS", 30.0
            ),
            minimum_free_bytes=_env_int(
                source, "BTC_OPTION_MIN_FREE_BYTES", 2_147_483_648
            ),
            log_level=source.get("BTC_OPTION_LOG_LEVEL", "INFO"),
            log_file=Path(log_file) if log_file else None,
            explicit_instruments=names,
            auth_scope=source.get(
                "DERIBIT_AUTH_SCOPE", "connection account:none trade:none wallet:none"
            ),
        )


@dataclass(slots=True)
class RuntimeState:
    started_at_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
    connected: bool = False
    authenticated: bool = False
    effective_scope: str | None = None
    last_error: str | None = None
    fatal_error: str | None = None
    fatal_exit_code: int = 1
    ticker_messages: int = 0
    ticker_messages_persisted: int = 0
    book_messages: int = 0
    lifecycle_messages: int = 0
    sequence_gaps: int = 0
    recommendation_runs: int = 0
    recommendation_candidates: int = 0
    alerts_sent: int = 0
    recommendation_state: str = "STARTING"
    last_recommendation_at_utc: datetime | None = None
    last_alert_at_utc: datetime | None = None
    last_alert_error: str | None = None
    refresh_requests: int = 0
    refresh_successes: int = 0
    refresh_writes: int = 0
    coherent_input_count: int = 0
    last_stream_event: str | None = None
    rejected_counts: dict[str, int] = field(default_factory=dict)
    last_refresh_reason: str | None = None
    last_refresh_diagnostics: dict[str, Any] = field(default_factory=dict)
    subscription_split: dict[str, int] = field(default_factory=dict)
    selected_instruments: tuple[str, ...] = ()
    selected_book_instruments: tuple[str, ...] = ()


def _evenly_spaced(items: list[Any], limit: int) -> list[Any]:
    if limit <= 0:
        return []
    if len(items) <= limit:
        return items
    if limit == 1:
        return [items[len(items) // 2]]
    indices = [round(index * (len(items) - 1) / (limit - 1)) for index in range(limit)]
    return [items[index] for index in indices]


def ticker_persistence_due(
    instrument_name: str,
    *,
    now_monotonic: float,
    last_persisted: dict[str, float],
    interval_seconds: float,
) -> bool:
    previous = last_persisted.get(instrument_name)
    return (
        previous is None
        or interval_seconds == 0
        or now_monotonic - previous >= interval_seconds
    )


def select_collection_universe(
    specs: dict[str, Any],
    *,
    now: datetime,
    min_dte_days: Decimal,
    max_dte_days: Decimal,
    buffer_days: float,
    limit: int,
    explicit_names: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Choose subscribed instruments with core decision-range priority.

    In non-explicit mode the core ``[min_dte, max_dte]`` decision range is
    selected first; only leftover slots are filled from the collection buffer
    window (``buffer_days`` around the core).  Buffered DTEs are research
    warm-up data and never relax the final decision-universe gate.
    """
    now_ms = int(now.timestamp() * 1000)
    core_min_ms = float(min_dte_days) * 86_400_000
    core_max_ms = float(max_dte_days) * 86_400_000
    buffer_min_ms = core_min_ms - buffer_days * 86_400_000
    buffer_max_ms = core_max_ms + buffer_days * 86_400_000
    explicit = set(explicit_names)
    core: list[Any] = []
    buffered: list[Any] = []
    for spec in specs.values():
        if spec.option_type != OptionType.CALL or spec.state != InstrumentState.OPEN:
            continue
        if explicit:
            if spec.instrument_name in explicit:
                core.append(spec)
            continue
        remaining_ms = spec.expiration_timestamp_ms - now_ms
        if core_min_ms <= remaining_ms <= core_max_ms:
            core.append(spec)
        elif buffer_min_ms <= remaining_ms <= buffer_max_ms:
            buffered.append(spec)
    core.sort(
        key=lambda item: (
            item.expiration_timestamp_ms,
            item.strike_usd_per_btc,
            item.instrument_name,
        )
    )
    buffered.sort(
        key=lambda item: (
            item.expiration_timestamp_ms,
            item.strike_usd_per_btc,
            item.instrument_name,
        )
    )
    if explicit or len(core) >= limit:
        return tuple(item.instrument_name for item in _evenly_spaced(core, limit))
    selected = [item.instrument_name for item in core]
    buffered_selected = _evenly_spaced(buffered, limit - len(selected))
    return tuple(selected + [item.instrument_name for item in buffered_selected])


def subscription_split_counts(
    specs: dict[str, Any],
    selected_names: tuple[str, ...],
    *,
    now: datetime,
    min_dte_days: Decimal,
    max_dte_days: Decimal,
) -> dict[str, int]:
    """Report how many subscription slots serve the core vs the buffer window."""
    now_ms = int(now.timestamp() * 1000)
    core_min_ms = float(min_dte_days) * 86_400_000
    core_max_ms = float(max_dte_days) * 86_400_000
    core = 0
    buffer_count = 0
    for name in selected_names:
        spec = specs.get(name)
        if spec is None:
            continue
        remaining_ms = spec.expiration_timestamp_ms - now_ms
        if core_min_ms <= remaining_ms <= core_max_ms:
            core += 1
        else:
            buffer_count += 1
    return {"core": core, "buffer": buffer_count}


async def refresh_instrument_batch(
    rest_client: Any,
    spine: PublicDataSpine,
    instrument_names: tuple[str, ...],
    *,
    max_concurrency: int,
    budget_seconds: float,
) -> tuple[dict[str, Any], list[str]]:
    """Actively fetch ``public/ticker`` for one bounded recommendation refresh set.

    Every successful response is persisted raw-first so a later ScannerRun can
    always resolve its lineage, even when the cycle itself is subsequently
    rejected as stale, skewed, or below cohort.  Total requests are bounded by
    the refresh set, in-flight work by the concurrency cap, and the whole batch
    by the per-cycle budget; any failure raises through to the caller.
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    observed: dict[str, Any] = {}
    errors: list[str] = []

    async def refresh_one(name: str) -> None:
        async with semaphore:
            try:
                envelope = await asyncio.to_thread(rest_client.ticker, name)
                observation = spine.ingest_ticker(
                    envelope,
                    transport=SourceTransport.HTTPS_REST,
                    channel="public/ticker",
                )
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                return
            observed[name] = observation

    tasks = [asyncio.create_task(refresh_one(name)) for name in instrument_names]
    if not tasks:
        return observed, errors
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=budget_seconds)
    except TimeoutError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return observed, errors


def _configure_logging(settings: CollectorSettings) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if settings.log_file is not None:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.WatchedFileHandler(settings.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    logging.Formatter.converter = time.gmtime


def _database_size(path: Path) -> int:
    return sum(
        candidate.stat().st_size
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        if candidate.exists()
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(temporary, path)


async def run_collector(
    settings: CollectorSettings,
    *,
    stop_event: asyncio.Event | None = None,
    credentials: DeribitCredentials | None = None,
    recommendation_settings: LiveRecommendationSettings | None = None,
    rest_client: DeribitPublicRestClient | Any | None = None,
    websocket_factory: Any | None = None,
) -> int:
    _configure_logging(settings)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    repository = SqliteRepository(settings.database_path)
    app_config = load_config(settings.config_path)
    recommendation_settings = (
        recommendation_settings or LiveRecommendationSettings.from_environment()
    )
    spine = PublicDataSpine(
        repository,
        stale_after_seconds=float(app_config.market_data.ticker_max_age_seconds),
    )
    credentials = credentials or DeribitCredentials.from_environment()
    if settings.book_interval == "raw" and credentials is None:
        repository.close()
        raise ValueError("BTC_OPTION_BOOK_INTERVAL=raw requires Deribit credentials")
    authenticator = (
        ReadOnlyWebSocketAuthenticator(credentials, settings.auth_scope)
        if credentials is not None
        else None
    )
    state = RuntimeState()
    stop_event = stop_event or asyncio.Event()
    refresh_event = asyncio.Event()
    stream: ReconnectingTickerStream | None = None
    book_trackers: dict[str, BookSequenceTracker] = {}
    last_ticker_persisted: dict[str, float] = {}
    latest_snapshots: dict[str, Any] = {}
    probe_offset = 0
    last_alert_monotonic: float | None = None
    last_alert_fingerprint: str | None = None
    initial_database_size = _database_size(settings.database_path)

    def save_system_event(name: str, detail: dict[str, Any], severity: str) -> None:
        observed = datetime.now(UTC)
        safe_detail = {key: value for key, value in detail.items() if "token" not in key.lower()}
        repository.save_system_event(
            SystemEvent(
                f"system:{uuid.uuid4()}",
                observed,
                observed,
                "collector.daemon",
                severity,
                name,
                json.dumps(safe_detail, sort_keys=True, default=str),
            )
        )

    async def on_system_event(name: str, detail: dict[str, Any]) -> None:
        if name == "CONNECTED":
            state.connected = True
            state.last_error = None
            state.recommendation_state = "WAITING_FOR_COHERENT_INPUTS"
            if recommendation_settings.enabled:
                write_recommendation_unavailable(
                    recommendation_settings,
                    state=DecisionState.DATA_ONLY.value,
                    reason="CONNECTED_WAITING_FOR_COHERENT_INPUTS",
                )
        elif name == "AUTHENTICATED":
            state.authenticated = True
            state.effective_scope = str(detail.get("scope"))
        elif name in {"DISCONNECTED", "HEARTBEAT_TIMEOUT", "AUTHENTICATION_FAILED"}:
            state.connected = False
            state.authenticated = False
            state.last_error = str(detail.get("error") or name)
            state.recommendation_state = "DATA_UNAVAILABLE"
            state.recommendation_candidates = 0
            state.last_stream_event = name
            if recommendation_settings.enabled:
                write_recommendation_unavailable(
                    recommendation_settings,
                    state=DecisionState.DATA_STALE.value,
                    reason=name,
                )
        if name == "AUTHENTICATION_FAILED":
            severity = "ERROR"
        elif name in {"DISCONNECTED", "HEARTBEAT_TIMEOUT"}:
            severity = "WARNING"
        else:
            severity = "INFO"
        save_system_event(name, detail, severity)
        level = {
            "ERROR": logging.ERROR,
            "WARNING": logging.WARNING,
        }.get(severity, logging.INFO)
        LOGGER.log(
            level,
            "%s %s",
            name,
            detail,
        )

    prefilter_policy = build_refresh_prefilter_policy(app_config, recommendation_settings)
    if (
        recommendation_settings.enabled
        and settings.refresh_limit < recommendation_settings.min_cohort_size
    ):
        raise ValueError("refresh limit must be at least the recommendation cohort minimum")

    async def run_recommendation_cycle() -> None:
        nonlocal last_alert_monotonic, last_alert_fingerprint, probe_offset
        if not recommendation_settings.enabled:
            state.recommendation_state = "DISABLED"
            return
        now_utc = datetime.now(UTC)
        if not state.connected:
            state.recommendation_state = "DATA_UNAVAILABLE"
            state.recommendation_candidates = 0
            write_recommendation_unavailable(
                recommendation_settings,
                state=DecisionState.DATA_STALE.value,
                reason=state.last_stream_event or "DISCONNECTED",
            )
            return
        cache = {name: observation.snapshot for name, observation in latest_snapshots.items()}
        subscribed_specs = {
            name: spine.specs[name]
            for name in state.selected_instruments
            if name in spine.specs
        }
        discovery = prefilter_refresh_candidates(
            subscribed_specs, cache, now_utc=now_utc, policy=prefilter_policy
        )
        refresh_names, rotation_count = build_refresh_set(
            discovery.proxy_valid,
            discovery.probe_pool,
            limit=settings.refresh_limit,
            probe_offset=probe_offset,
        )
        probe_offset += max(1, rotation_count)
        diagnostics = {
            "prefilter_policy_version": prefilter_policy.version,
            "decision_universe_count": len(discovery.decision_universe),
            "cache_instrument_count": len(cache),
            "proxy_valid_count": len(discovery.proxy_valid),
            "rotation_probe_count": rotation_count,
            "refresh_set_count": len(refresh_names),
        }

        def record_unavailable(reason: str, decision_state: DecisionState) -> None:
            diagnostics["reason"] = reason
            state.recommendation_state = reason
            state.recommendation_candidates = 0
            state.coherent_input_count = 0
            state.last_refresh_reason = reason
            state.last_refresh_diagnostics = dict(diagnostics)
            write_recommendation_unavailable(
                recommendation_settings,
                state=decision_state.value,
                reason=reason,
                diagnostics=diagnostics,
            )

        if len(refresh_names) < recommendation_settings.min_cohort_size:
            record_unavailable(REASON_CACHE_ONLY, DecisionState.DATA_ONLY)
            return
        if rest_client.base_url != rest_base_url_for_websocket(settings.websocket_url):
            record_unavailable(REASON_ENVIRONMENT_MISMATCH, DecisionState.DATA_ONLY)
            return
        state.refresh_requests += len(refresh_names)
        refresh_started_monotonic = time.monotonic()
        try:
            refreshed, errors = await refresh_instrument_batch(
                rest_client,
                spine,
                refresh_names,
                max_concurrency=settings.refresh_max_concurrency,
                budget_seconds=settings.refresh_cycle_budget_seconds,
            )
        except TimeoutError:
            record_unavailable(REASON_REFRESH_TIMEOUT, DecisionState.DATA_ONLY)
            return
        diagnostics["refresh_duration_seconds"] = round(
            time.monotonic() - refresh_started_monotonic, 3
        )
        state.refresh_successes += len(refreshed)
        state.refresh_writes += len(refreshed)
        diagnostics["refresh_success_count"] = len(refreshed)
        if errors:
            diagnostics["refresh_errors"] = errors[:10]
            record_unavailable(REASON_REFRESH_FAILED, DecisionState.DATA_ONLY)
            return
        evaluation_as_of_utc = datetime.now(UTC)
        coherent, rejected = select_coherent_snapshots_with_diagnostics(
            tuple(refreshed.values()),
            as_of_utc=evaluation_as_of_utc,
            max_snapshot_skew_ms=app_config.market_data.max_snapshot_skew_ms,
            max_age_seconds=float(app_config.market_data.ticker_max_age_seconds),
        )
        for code, count in rejected.items():
            if count:
                state.rejected_counts[code] = state.rejected_counts.get(code, 0) + count
        diagnostics["coherent_input_count"] = len(coherent)
        diagnostics["rejected_counts"] = rejected
        state.coherent_input_count = len(coherent)
        if len(coherent) < recommendation_settings.min_cohort_size:
            if not coherent and rejected.get(REASON_SNAPSHOT_SKEW, 0):
                reason = REASON_SNAPSHOT_SKEW
                decision_state = DecisionState.DATA_ONLY
            elif not coherent and rejected.get(REASON_SNAPSHOT_STALE, 0):
                reason = REASON_SNAPSHOT_STALE
                decision_state = DecisionState.DATA_STALE
            else:
                reason = REASON_COHORT_SAMPLE_BELOW_POLICY
                decision_state = DecisionState.DATA_ONLY
            record_unavailable(reason, decision_state)
            return
        option_types = {name: spec.option_type for name, spec in spine.specs.items()}
        try:
            result = evaluate_live_recommendations(
                coherent,
                option_types,
                config=app_config,
                repository=repository,
                settings=recommendation_settings,
                as_of_utc=evaluation_as_of_utc,
            )
        except Exception as exc:
            state.recommendation_state = "FAILED_INTERNAL"
            state.recommendation_candidates = 0
            save_system_event("RECOMMENDATION_FAILED", {"error": str(exc)}, "ERROR")
            write_recommendation_unavailable(
                recommendation_settings,
                state=DecisionState.DATA_ONLY.value,
                reason="RECOMMENDATION_FAILED",
            )
            LOGGER.exception("live recommendation failed")
            return
        state.recommendation_runs += 1
        state.recommendation_candidates = result.candidate_count
        state.recommendation_state = str(result.payload["decision_state"])
        state.last_recommendation_at_utc = evaluation_as_of_utc
        state.last_refresh_reason = None
        state.last_refresh_diagnostics = dict(diagnostics)
        if not recommendation_settings.feishu_enabled or not result.notification_text:
            return
        if not alert_delivery_due(
            now_monotonic=time.monotonic(),
            last_alert_monotonic=last_alert_monotonic,
            minimum_interval_seconds=recommendation_settings.minimum_alert_interval_seconds,
            reminder_seconds=recommendation_settings.reminder_seconds,
            fingerprint=result.top_fingerprint,
            last_fingerprint=last_alert_fingerprint,
        ):
            return
        try:
            await send_feishu_message(recommendation_settings, result.notification_text)
        except Exception as exc:
            state.last_alert_error = str(exc)
            save_system_event("FEISHU_ALERT_FAILED", {"error": str(exc)}, "ERROR")
            LOGGER.warning("Feishu alert failed: %s", exc)
        else:
            last_alert_monotonic = time.monotonic()
            last_alert_fingerprint = result.top_fingerprint
            state.alerts_sent += 1
            state.last_alert_at_utc = now_utc
            state.last_alert_error = None

    async def on_message(message: dict[str, Any]) -> None:
        nonlocal stream
        params = message.get("params")
        channel = params.get("channel") if isinstance(params, dict) else None
        if not isinstance(channel, str):
            return
        try:
            if channel.startswith("ticker."):
                state.ticker_messages += 1
                data = params.get("data") if isinstance(params, dict) else None
                instrument_name = (
                    data.get("instrument_name") if isinstance(data, dict) else None
                )
                # Every legal ticker updates the in-memory discovery cache first;
                # persistence stays independently sampled per instrument.
                observation = spine.observe_ticker(
                    message,
                    transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
                    channel=channel,
                )
                if not isinstance(instrument_name, str):
                    return
                latest_snapshots[instrument_name] = observation
                now_monotonic = time.monotonic()
                if not ticker_persistence_due(
                    instrument_name,
                    now_monotonic=now_monotonic,
                    last_persisted=last_ticker_persisted,
                    interval_seconds=settings.ticker_persist_interval_seconds,
                ):
                    return
                spine.persist_observed_ticker(observation)
                last_ticker_persisted[instrument_name] = now_monotonic
                state.ticker_messages_persisted += 1
            elif channel.startswith("book."):
                state.book_messages += 1
                instrument_name = channel.split(".", 2)[1]
                tracker = book_trackers.get(instrument_name)
                if tracker is None:
                    raw = record_raw_event(
                        message,
                        transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
                        channel_or_method=channel,
                    )
                    repository.save_raw_event(raw)
                    refresh_event.set()
                    return
                result = tracker.apply(message, channel=channel)
                if result.resync_required:
                    state.sequence_gaps += int(result.status == "SEQUENCE_GAP")
                    if stream is not None:
                        stream.request_reconnect("book resynchronization required")
            elif channel == "instrument.state.option.BTC":
                state.lifecycle_messages += 1
                spine.ingest_lifecycle(message, channel=channel)
                refresh_event.set()
        except (NormalizationError, ValueError) as exc:
            state.last_error = str(exc)
            LOGGER.warning("message rejected fail-closed: %s", exc)
            if channel == "instrument.state.option.BTC":
                refresh_event.set()

    rest_client = rest_client or DeribitPublicRestClient(
        rest_base_url_for_websocket(settings.websocket_url),
        config=RateLimitConfig(
            requests_per_second=settings.refresh_requests_per_second,
            timeout_seconds=settings.refresh_timeout_seconds,
        ),
    )

    async def refresh_catalog() -> bool:
        response = await asyncio.to_thread(rest_client.get_instruments, "BTC", False)
        result = response.get("result")
        if not isinstance(result, list):
            raise RuntimeError("Deribit catalog result must be a list")
        for item in result:
            if isinstance(item, dict):
                spine.ingest_instrument(
                    item,
                    transport=SourceTransport.HTTPS_REST,
                    channel="public/get_instruments",
                )
        selected = select_collection_universe(
            spine.specs,
            now=datetime.now(UTC),
            min_dte_days=app_config.universe.min_dte_days,
            max_dte_days=app_config.universe.max_dte_days,
            buffer_days=settings.collection_buffer_days,
            limit=settings.max_instruments,
            explicit_names=settings.explicit_instruments,
        )
        books = tuple(_evenly_spaced(list(selected), settings.max_book_instruments))
        changed = (
            selected != state.selected_instruments or books != state.selected_book_instruments
        )
        state.selected_instruments = selected
        state.selected_book_instruments = books
        state.subscription_split = subscription_split_counts(
            spine.specs,
            selected,
            now=datetime.now(UTC),
            min_dte_days=app_config.universe.min_dte_days,
            max_dte_days=app_config.universe.max_dte_days,
        )
        return changed

    async def catalog_loop() -> None:
        nonlocal book_trackers
        while not stop_event.is_set():
            refresh_event.clear()
            wait_refresh = asyncio.create_task(refresh_event.wait())
            wait_stop = asyncio.create_task(stop_event.wait())
            try:
                await asyncio.wait(
                    {wait_refresh, wait_stop},
                    timeout=settings.catalog_refresh_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in (wait_refresh, wait_stop):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(wait_refresh, wait_stop, return_exceptions=True)
            if stop_event.is_set():
                return
            try:
                changed = await refresh_catalog()
                book_trackers = {
                    name: book_trackers.get(name, BookSequenceTracker(repository, name))
                    for name in state.selected_book_instruments
                }
                if changed and stream is not None:
                    stream.set_subscriptions(
                        {
                            TickerSubscription(name, settings.ticker_interval)
                            for name in state.selected_instruments
                        }
                    )
                    stream.set_book_subscriptions(
                        {
                            BookSubscription(name, settings.book_interval)
                            for name in state.selected_book_instruments
                        }
                    )
                    stream.request_reconnect("catalog subscription set changed")
            except Exception as exc:
                state.last_error = f"catalog refresh failed: {exc}"
                save_system_event("CATALOG_REFRESH_FAILED", {"error": str(exc)}, "ERROR")
                LOGGER.exception("catalog refresh failed")

    async def recommendation_loop() -> None:
        """Independent recommendation cadence; never depends on a ticker callback."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=recommendation_settings.interval_seconds
                )
            except TimeoutError:
                await run_recommendation_cycle()
                continue
            return

    async def maintenance_loop() -> None:
        while not stop_event.is_set():
            try:
                cutoff = datetime.now(UTC) - timedelta(days=settings.retention_days)
                pruned = repository.prune_before(cutoff)
                repository.checkpoint()
                free_bytes = shutil.disk_usage(settings.database_path.parent).free
                if any(pruned.values()):
                    save_system_event("RETENTION_APPLIED", {"deleted": pruned}, "INFO")
                    LOGGER.info("retention applied: %s", pruned)
                if free_bytes < settings.minimum_free_bytes:
                    state.fatal_error = (
                        f"free disk {free_bytes} below minimum {settings.minimum_free_bytes}"
                    )
                    state.fatal_exit_code = 78
                    save_system_event(
                        "LOW_DISK_FAIL_CLOSED",
                        {
                            "free_bytes": free_bytes,
                            "minimum_free_bytes": settings.minimum_free_bytes,
                        },
                        "CRITICAL",
                    )
                    stop_event.set()
                    return
            except Exception as exc:
                state.last_error = f"maintenance failed: {exc}"
                save_system_event("MAINTENANCE_FAILED", {"error": str(exc)}, "ERROR")
                LOGGER.exception("maintenance failed")
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=settings.retention_check_seconds
                )
            except TimeoutError:
                continue

    def status_payload() -> dict[str, Any]:
        database_size = _database_size(settings.database_path)
        elapsed_seconds = max(
            1.0, (datetime.now(UTC) - state.started_at_utc).total_seconds()
        )
        estimated_daily_growth = int(
            max(0, database_size - initial_database_size) / elapsed_seconds * 86_400
        )
        return {
            "schema_version": "collector-status-v3",
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "started_at_utc": state.started_at_utc.isoformat(),
            "connected": state.connected,
            "authentication_enabled": credentials is not None,
            "authenticated": state.authenticated,
            "effective_scope": state.effective_scope,
            "last_error": state.last_error,
            "fatal_error": state.fatal_error,
            "seconds_since_message": stream.seconds_since_message if stream is not None else None,
            "selected_instrument_count": len(state.selected_instruments),
            "selected_book_instrument_count": len(state.selected_book_instruments),
            "subscription_split": dict(state.subscription_split),
            "cache_instrument_count": len(latest_snapshots),
            "message_counts": {
                "ticker_received": state.ticker_messages,
                "ticker_persisted": state.ticker_messages_persisted,
                "book": state.book_messages,
                "lifecycle": state.lifecycle_messages,
                "sequence_gaps": state.sequence_gaps,
            },
            "recommendation": {
                "enabled": recommendation_settings.enabled,
                "state": state.recommendation_state,
                "runs": state.recommendation_runs,
                "eligible_candidates": state.recommendation_candidates,
                "last_run_at_utc": (
                    state.last_recommendation_at_utc.isoformat()
                    if state.last_recommendation_at_utc
                    else None
                ),
                "output_path": str(recommendation_settings.output_path),
                "feishu_enabled": recommendation_settings.feishu_enabled,
                "alerts_sent": state.alerts_sent,
                "last_alert_at_utc": (
                    state.last_alert_at_utc.isoformat() if state.last_alert_at_utc else None
                ),
                "last_alert_error": state.last_alert_error,
                "minimum_alert_interval_seconds": (
                    recommendation_settings.minimum_alert_interval_seconds
                ),
                "reminder_seconds": recommendation_settings.reminder_seconds,
                "refresh": {
                    "prefilter_policy_version": prefilter_policy.version,
                    "limit": settings.refresh_limit,
                    "requests_per_second": settings.refresh_requests_per_second,
                    "request_timeout_seconds": settings.refresh_timeout_seconds,
                    "cycle_budget_seconds": settings.refresh_cycle_budget_seconds,
                    "max_concurrency": settings.refresh_max_concurrency,
                    "requests_total": state.refresh_requests,
                    "successes_total": state.refresh_successes,
                    "writes_total": state.refresh_writes,
                    "coherent_input_count": state.coherent_input_count,
                    "rejected_counts": dict(state.rejected_counts),
                    "last_reason": state.last_refresh_reason,
                    "last_diagnostics": dict(state.last_refresh_diagnostics),
                },
                "quality_policy": {
                    "min_dte_days": str(app_config.universe.min_dte_days),
                    "max_dte_days": str(app_config.universe.max_dte_days),
                    "min_absolute_delta": str(
                        recommendation_settings.min_absolute_delta
                    ),
                    "max_absolute_delta": str(
                        recommendation_settings.max_absolute_delta
                    ),
                    "max_relative_spread": str(
                        recommendation_settings.max_relative_spread
                    ),
                    "min_cohort_size": recommendation_settings.min_cohort_size,
                },
            },
            "database_counts": repository.counts(),
            "database_size_bytes": database_size,
            "estimated_database_growth_bytes_per_day": estimated_daily_growth,
            "disk_free_bytes": shutil.disk_usage(settings.database_path.parent).free,
        }

    async def status_loop() -> None:
        while not stop_event.is_set():
            _write_json_atomic(settings.status_path, status_payload())
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=settings.status_interval_seconds
                )
            except TimeoutError:
                continue

    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(handled_signal, stop_event.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(
                handled_signal,
                lambda _number, _frame: loop.call_soon_threadsafe(stop_event.set),
            )

    try:
        await refresh_catalog()
        initial_database_size = _database_size(settings.database_path)
        book_trackers = {
            name: BookSequenceTracker(repository, name)
            for name in state.selected_book_instruments
        }
        stream = ReconnectingTickerStream(
            websocket_factory
            or (lambda: connect_public_websocket(settings.websocket_url)),
            on_message,
            on_system_event,
            heartbeat_timeout_seconds=float(app_config.market_data.heartbeat_timeout_seconds),
            authenticator=authenticator,
        )
        stream.set_subscriptions(
            {
                TickerSubscription(name, settings.ticker_interval)
                for name in state.selected_instruments
            }
        )
        stream.set_book_subscriptions(
            {
                BookSubscription(name, settings.book_interval)
                for name in state.selected_book_instruments
            }
        )
        LOGGER.info(
            "collector starting: profile=%s tickers=%d books=%d persist_interval=%.1fs "
            "authenticated=%s",
            settings.collection_profile,
            len(state.selected_instruments),
            len(state.selected_book_instruments),
            settings.ticker_persist_interval_seconds,
            credentials is not None,
        )
        LOGGER.info(
            "live recommendations: enabled=%s interval=%.1fs feishu=%s",
            recommendation_settings.enabled,
            recommendation_settings.interval_seconds,
            recommendation_settings.feishu_enabled,
        )
        tasks = [
            asyncio.create_task(stream.run(), name="market-stream"),
            asyncio.create_task(catalog_loop(), name="catalog-refresh"),
            asyncio.create_task(recommendation_loop(), name="recommendation"),
            asyncio.create_task(maintenance_loop(), name="retention"),
            asyncio.create_task(status_loop(), name="status"),
        ]
        stop_wait = asyncio.create_task(stop_event.wait(), name="shutdown-signal")
        done, _ = await asyncio.wait({stop_wait, *tasks}, return_when=asyncio.FIRST_COMPLETED)
        if not stop_event.is_set():
            for task in done:
                if task is not stop_wait and not task.cancelled():
                    exception = task.exception()
                    state.fatal_error = (
                        str(exception) if exception else f"{task.get_name()} stopped"
                    )
                    if isinstance(exception, AuthenticationError):
                        state.fatal_exit_code = 78
                    stop_event.set()
        stream.stop()
        stream.request_reconnect("collector shutdown")
        await asyncio.gather(*tasks, return_exceptions=True)
        stop_wait.cancel()
        await asyncio.gather(stop_wait, return_exceptions=True)
        state.connected = False
        state.authenticated = False
        state.recommendation_state = "DATA_UNAVAILABLE"
        state.recommendation_candidates = 0
        if recommendation_settings.enabled:
            write_recommendation_unavailable(
                recommendation_settings,
                state=DecisionState.DATA_STALE.value,
                reason="COLLECTOR_STOPPED",
            )
        _write_json_atomic(settings.status_path, status_payload())
        return state.fatal_exit_code if state.fatal_error else 0
    except Exception as exc:
        state.fatal_error = str(exc)
        LOGGER.exception("collector failed during startup")
        try:
            if recommendation_settings.enabled:
                write_recommendation_unavailable(
                    recommendation_settings,
                    state=DecisionState.DATA_STALE.value,
                    reason="COLLECTOR_FAILED",
                )
            _write_json_atomic(settings.status_path, status_payload())
        except Exception:
            LOGGER.exception("could not write failed startup status")
        return 1
    finally:
        repository.checkpoint()
        repository.close()


def healthcheck(path: Path, *, max_age_seconds: float) -> tuple[bool, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(payload["updated_at_utc"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return False, {"status": "UNHEALTHY", "reason": f"invalid status file: {exc}"}
    age = (datetime.now(UTC) - updated).total_seconds()
    reasons = []
    if age > max_age_seconds:
        reasons.append(f"status age {age:.1f}s exceeds {max_age_seconds:.1f}s")
    if not payload.get("connected"):
        reasons.append("collector is disconnected")
    if payload.get("fatal_error"):
        reasons.append(str(payload["fatal_error"]))
    message_age = payload.get("seconds_since_message")
    if not isinstance(message_age, int | float) or message_age > max_age_seconds:
        reasons.append("no recent WebSocket message or heartbeat")
    return not reasons, {
        "status": "HEALTHY" if not reasons else "UNHEALTHY",
        "status_age_seconds": round(age, 3),
        "reasons": reasons,
    }


def main() -> int:
    try:
        settings = CollectorSettings.from_environment()
        return asyncio.run(run_collector(settings))
    except (AuthenticationError, ValueError) as exc:
        print(f"invalid collector configuration: {exc}", file=sys.stderr)
        return 78 if isinstance(exc, AuthenticationError) else 2


def healthcheck_main() -> int:
    parser = argparse.ArgumentParser(description="Check the collector status file")
    parser.add_argument(
        "--status",
        type=Path,
        default=Path(os.getenv("BTC_OPTION_STATUS_PATH", "runtime/collector-status.json")),
    )
    parser.add_argument(
        "--max-age-seconds",
        type=float,
        default=float(os.getenv("BTC_OPTION_HEALTH_MAX_AGE_SECONDS", "120")),
    )
    args = parser.parse_args()
    healthy, payload = healthcheck(args.status, max_age_seconds=args.max_age_seconds)
    print(json.dumps(payload, sort_keys=True))
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
