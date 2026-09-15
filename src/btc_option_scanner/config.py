"""Versioned, validated, research-only application configuration."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .domain.primitives import CORE_METRIC_VERSION, DOMAIN_SCHEMA_VERSION


class ConfigError(ValueError):
    """Raised when configuration is missing, malformed, or unsafe."""


@dataclass(frozen=True, slots=True)
class ExperimentalDefault:
    source: str
    rationale: str


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    enabled: bool
    mode: str

    def __post_init__(self) -> None:
        if self.enabled is not False:
            raise ConfigError("execution.enabled must remain false in v1")
        if self.mode != "research_only":
            raise ConfigError("execution.mode must be research_only in v1")


@dataclass(frozen=True, slots=True)
class MarketDataConfig:
    public_only: bool
    base_currency: str
    ticker_max_age_seconds: Decimal
    heartbeat_timeout_seconds: Decimal
    max_clock_skew_seconds: Decimal
    max_snapshot_skew_ms: int
    experimental: ExperimentalDefault

    def __post_init__(self) -> None:
        if self.public_only is not True:
            raise ConfigError("market_data.public_only must remain true")
        if self.base_currency != "BTC":
            raise ConfigError("v1 market data base_currency must be BTC")
        _positive(self.ticker_max_age_seconds, "ticker_max_age_seconds")
        _positive(self.heartbeat_timeout_seconds, "heartbeat_timeout_seconds")
        _positive(self.max_clock_skew_seconds, "max_clock_skew_seconds")
        if self.max_snapshot_skew_ms <= 0:
            raise ConfigError("max_snapshot_skew_ms must be positive")


@dataclass(frozen=True, slots=True)
class UniverseConfig:
    option_type: str
    min_dte_days: Decimal
    max_dte_days: Decimal
    experimental: ExperimentalDefault

    def __post_init__(self) -> None:
        if self.option_type != "CALL":
            raise ConfigError("v1 decision universe only permits CALL")
        _nonnegative(self.min_dte_days, "min_dte_days")
        _positive(self.max_dte_days, "max_dte_days")
        if self.min_dte_days > self.max_dte_days:
            raise ConfigError("min_dte_days must not exceed max_dte_days")


@dataclass(frozen=True, slots=True)
class ScannerConfig:
    min_open_interest_base_btc: Decimal
    max_spread_fraction: Decimal
    min_cohort_size: int
    experimental: ExperimentalDefault

    def __post_init__(self) -> None:
        _nonnegative(self.min_open_interest_base_btc, "min_open_interest_base_btc")
        if not Decimal("0") < self.max_spread_fraction <= Decimal("1"):
            raise ConfigError("max_spread_fraction must be in (0, 1]")
        if self.min_cohort_size < 1:
            raise ConfigError("min_cohort_size must be at least 1")


@dataclass(frozen=True, slots=True)
class FeatureGates:
    local_pricing_production: bool
    gamma_metrics: bool
    utility_ranking: bool
    executable_roll_cashflows: bool
    automatic_execution: bool

    def __post_init__(self) -> None:
        enabled = [name for name, value in asdict(self).items() if value]
        if enabled:
            raise ConfigError(
                f"unreleased v1 feature gates cannot be enabled: {', '.join(enabled)}"
            )


@dataclass(frozen=True, slots=True)
class AppConfig:
    config_label: str
    schema_version: str
    metric_version: str
    execution: ExecutionConfig
    market_data: MarketDataConfig
    universe: UniverseConfig
    scanner: ScannerConfig
    feature_gates: FeatureGates

    def __post_init__(self) -> None:
        if not self.config_label.strip():
            raise ConfigError("config_label must not be empty")
        if self.schema_version != DOMAIN_SCHEMA_VERSION:
            raise ConfigError(f"schema_version must be {DOMAIN_SCHEMA_VERSION}")
        if self.metric_version != CORE_METRIC_VERSION:
            raise ConfigError(f"metric_version must be {CORE_METRIC_VERSION}")

    @property
    def config_version(self) -> str:
        """Content fingerprint required by domain-contract-v1 §10.1."""
        payload = json.dumps(asdict(self), default=str, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{self.config_label}+sha256:{digest}"


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load one complete YAML config; unknown keys fail instead of being ignored."""
    source = (
        Path(path)
        if path is not None
        else Path(os.getenv("BTC_OPTION_SCANNER_CONFIG", "config.yaml"))
    )
    try:
        with source.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read configuration {source}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a mapping")
    _keys(
        raw,
        {
            "config_label",
            "schema_version",
            "metric_version",
            "execution",
            "market_data",
            "universe",
            "scanner",
            "feature_gates",
        },
        "root",
    )
    return AppConfig(
        config_label=_string(raw["config_label"], "config_label"),
        schema_version=_string(raw["schema_version"], "schema_version"),
        metric_version=_string(raw["metric_version"], "metric_version"),
        execution=_execution(raw["execution"]),
        market_data=_market_data(raw["market_data"]),
        universe=_universe(raw["universe"]),
        scanner=_scanner(raw["scanner"]),
        feature_gates=_feature_gates(raw["feature_gates"]),
    )


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    missing, unknown = expected - actual, actual - expected
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing: {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"unknown: {', '.join(sorted(unknown))}")
        raise ConfigError(f"invalid keys in {name} ({'; '.join(details)})")


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ConfigError(f"{name} must be boolean")
    return value


def _decimal(value: Any, name: str) -> Decimal:
    if type(value) is bool:
        raise ConfigError(f"{name} must be a finite decimal")
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"{name} must be a finite decimal") from exc
    if not converted.is_finite():
        raise ConfigError(f"{name} must be a finite decimal")
    return converted


def _positive(value: Decimal, name: str) -> None:
    if value <= 0:
        raise ConfigError(f"{name} must be positive")


def _nonnegative(value: Decimal, name: str) -> None:
    if value < 0:
        raise ConfigError(f"{name} must be non-negative")


def _experimental(value: Any, name: str) -> ExperimentalDefault:
    raw = _mapping(value, name)
    _keys(raw, {"source", "rationale"}, name)
    return ExperimentalDefault(
        _string(raw["source"], f"{name}.source"), _string(raw["rationale"], f"{name}.rationale")
    )


def _execution(value: Any) -> ExecutionConfig:
    raw = _mapping(value, "execution")
    _keys(raw, {"enabled", "mode"}, "execution")
    return ExecutionConfig(
        _bool(raw["enabled"], "execution.enabled"), _string(raw["mode"], "execution.mode")
    )


def _market_data(value: Any) -> MarketDataConfig:
    raw = _mapping(value, "market_data")
    expected = {
        "public_only",
        "base_currency",
        "ticker_max_age_seconds",
        "heartbeat_timeout_seconds",
        "max_clock_skew_seconds",
        "max_snapshot_skew_ms",
        "experimental",
    }
    _keys(raw, expected, "market_data")
    skew = raw["max_snapshot_skew_ms"]
    if type(skew) is not int:
        raise ConfigError("market_data.max_snapshot_skew_ms must be an integer")
    return MarketDataConfig(
        _bool(raw["public_only"], "market_data.public_only"),
        _string(raw["base_currency"], "market_data.base_currency"),
        _decimal(raw["ticker_max_age_seconds"], "market_data.ticker_max_age_seconds"),
        _decimal(raw["heartbeat_timeout_seconds"], "market_data.heartbeat_timeout_seconds"),
        _decimal(raw["max_clock_skew_seconds"], "market_data.max_clock_skew_seconds"),
        skew,
        _experimental(raw["experimental"], "market_data.experimental"),
    )


def _universe(value: Any) -> UniverseConfig:
    raw = _mapping(value, "universe")
    _keys(raw, {"option_type", "min_dte_days", "max_dte_days", "experimental"}, "universe")
    return UniverseConfig(
        _string(raw["option_type"], "universe.option_type"),
        _decimal(raw["min_dte_days"], "universe.min_dte_days"),
        _decimal(raw["max_dte_days"], "universe.max_dte_days"),
        _experimental(raw["experimental"], "universe.experimental"),
    )


def _scanner(value: Any) -> ScannerConfig:
    raw = _mapping(value, "scanner")
    _keys(
        raw,
        {"min_open_interest_base_btc", "max_spread_fraction", "min_cohort_size", "experimental"},
        "scanner",
    )
    cohort = raw["min_cohort_size"]
    if type(cohort) is not int:
        raise ConfigError("scanner.min_cohort_size must be an integer")
    return ScannerConfig(
        _decimal(raw["min_open_interest_base_btc"], "scanner.min_open_interest_base_btc"),
        _decimal(raw["max_spread_fraction"], "scanner.max_spread_fraction"),
        cohort,
        _experimental(raw["experimental"], "scanner.experimental"),
    )


def _feature_gates(value: Any) -> FeatureGates:
    raw = _mapping(value, "feature_gates")
    names = {
        "local_pricing_production",
        "gamma_metrics",
        "utility_ranking",
        "executable_roll_cashflows",
        "automatic_execution",
    }
    _keys(raw, names, "feature_gates")
    return FeatureGates(**{name: _bool(raw[name], f"feature_gates.{name}") for name in names})
