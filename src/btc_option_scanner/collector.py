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
    ReadOnlyWebSocketAuthenticator,
    ReconnectingTickerStream,
    TickerSubscription,
    connect_public_websocket,
)
from .deribit.parser import record_raw_event
from .domain import InstrumentState, OptionType, SourceTransport, SystemEvent

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
    max_instruments: int = 180
    max_book_instruments: int = 20
    collection_buffer_days: float = 30.0
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
        if self.collection_buffer_days < 0 or self.minimum_free_bytes < 1:
            raise ValueError("collection buffer and minimum free bytes are invalid")
        if self.log_level.upper() not in logging.getLevelNamesMapping():
            raise ValueError("unknown log level")

    @classmethod
    def from_environment(
        cls, environment: dict[str, str] | None = None
    ) -> CollectorSettings:
        source = dict(os.environ if environment is None else environment)
        names = tuple(
            item.strip()
            for item in source.get("BTC_OPTION_INSTRUMENTS", "").split(",")
            if item.strip()
        )
        log_file = source.get("BTC_OPTION_LOG_FILE", "").strip()
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
            max_instruments=_env_int(source, "BTC_OPTION_MAX_INSTRUMENTS", 180),
            max_book_instruments=_env_int(source, "BTC_OPTION_MAX_BOOK_INSTRUMENTS", 20),
            collection_buffer_days=_env_float(
                source, "BTC_OPTION_COLLECTION_BUFFER_DAYS", 30.0
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
    book_messages: int = 0
    lifecycle_messages: int = 0
    sequence_gaps: int = 0
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
    now_ms = int(now.timestamp() * 1000)
    min_ms = float(min_dte_days) * 86_400_000 - buffer_days * 86_400_000
    max_ms = float(max_dte_days) * 86_400_000 + buffer_days * 86_400_000
    explicit = set(explicit_names)
    candidates = [
        spec
        for spec in specs.values()
        if spec.option_type == OptionType.CALL
        and spec.state == InstrumentState.OPEN
        and (
            spec.instrument_name in explicit
            if explicit
            else min_ms <= spec.expiration_timestamp_ms - now_ms <= max_ms
        )
    ]
    candidates.sort(
        key=lambda item: (
            item.expiration_timestamp_ms,
            item.strike_usd_per_btc,
            item.instrument_name,
        )
    )
    return tuple(item.instrument_name for item in _evenly_spaced(candidates, limit))


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
) -> int:
    _configure_logging(settings)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    repository = SqliteRepository(settings.database_path)
    app_config = load_config(settings.config_path)
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
        elif name == "AUTHENTICATED":
            state.authenticated = True
            state.effective_scope = str(detail.get("scope"))
        elif name in {"DISCONNECTED", "HEARTBEAT_TIMEOUT", "AUTHENTICATION_FAILED"}:
            state.connected = False
            state.authenticated = False
            state.last_error = str(detail.get("error") or name)
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

    async def on_message(message: dict[str, Any]) -> None:
        nonlocal stream
        params = message.get("params")
        channel = params.get("channel") if isinstance(params, dict) else None
        if not isinstance(channel, str):
            return
        try:
            if channel.startswith("ticker."):
                state.ticker_messages += 1
                spine.ingest_ticker(
                    message,
                    transport=SourceTransport.WEBSOCKET_SUBSCRIPTION,
                    channel=channel,
                )
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

    rest_client = DeribitPublicRestClient()

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
        return {
            "schema_version": "collector-status-v1",
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
            "message_counts": {
                "ticker": state.ticker_messages,
                "book": state.book_messages,
                "lifecycle": state.lifecycle_messages,
                "sequence_gaps": state.sequence_gaps,
            },
            "database_counts": repository.counts(),
            "database_size_bytes": _database_size(settings.database_path),
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
        book_trackers = {
            name: BookSequenceTracker(repository, name)
            for name in state.selected_book_instruments
        }
        stream = ReconnectingTickerStream(
            lambda: connect_public_websocket(settings.websocket_url),
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
            "collector starting: tickers=%d books=%d authenticated=%s",
            len(state.selected_instruments),
            len(state.selected_book_instruments),
            credentials is not None,
        )
        tasks = [
            asyncio.create_task(stream.run(), name="market-stream"),
            asyncio.create_task(catalog_loop(), name="catalog-refresh"),
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
        _write_json_atomic(settings.status_path, status_payload())
        return state.fatal_exit_code if state.fatal_error else 0
    except Exception as exc:
        state.fatal_error = str(exc)
        LOGGER.exception("collector failed during startup")
        try:
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
