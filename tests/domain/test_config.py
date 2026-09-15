from __future__ import annotations

from pathlib import Path

import pytest

from btc_option_scanner.config import ConfigError, load_config


def test_default_configuration_is_research_only_and_fingerprinted(project_root: Path) -> None:
    config = load_config(project_root / "config.yaml")

    assert config.execution.enabled is False
    assert config.execution.mode == "research_only"
    assert config.feature_gates.automatic_execution is False
    assert config.feature_gates.executable_roll_cashflows is False
    assert config.config_version.startswith("experimental-defaults-v1+sha256:")
    assert config.config_version == load_config(project_root / "config.yaml").config_version


@pytest.mark.parametrize(
    ("needle", "replacement"),
    [
        ("enabled: false", "enabled: true"),
        ("automatic_execution: false", "automatic_execution: true"),
        ("max_spread_fraction: 0.10", "max_spread_fraction: 1.1"),
    ],
)
def test_unsafe_or_illegal_values_are_rejected(
    project_root: Path, tmp_path: Path, needle: str, replacement: str
) -> None:
    target = tmp_path / "invalid.yaml"
    target.write_text(
        (project_root / "config.yaml").read_text(encoding="utf-8").replace(needle, replacement),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(target)


def test_unknown_config_key_is_rejected(project_root: Path, tmp_path: Path) -> None:
    target = tmp_path / "unknown.yaml"
    target.write_text(
        (project_root / "config.yaml").read_text(encoding="utf-8") + "\nunknown: value\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown"):
        load_config(target)
