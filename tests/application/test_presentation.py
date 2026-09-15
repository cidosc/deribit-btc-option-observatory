from __future__ import annotations

import json

from btc_option_scanner.application import (
    PresentationSnapshot,
    combine_snapshots,
    load_presentation_snapshot,
    save_presentation_snapshot,
)


def test_snapshot_round_trip_preserves_shared_cli_dashboard_data(tmp_path) -> None:
    source = PresentationSnapshot(
        "2026-09-15T00:00:00+00:00",
        "core-metrics@1.0.0",
        "test+sha256:x",
        {"scanner": {"candidates": [{"instrument_name": "BTC-X-C", "state": "DATA_STALE"}]}},
    )
    target = tmp_path / "snapshot.json"
    save_presentation_snapshot(source, target)
    loaded = load_presentation_snapshot(target)
    assert loaded.to_dict() == source.to_dict()
    assert (
        json.loads(target.read_text(encoding="utf-8"))["sections"]["scanner"]["candidates"][0][
            "state"
        ]
        == "DATA_STALE"
    )


def test_missing_section_is_not_silently_filled() -> None:
    snapshot = PresentationSnapshot("t", "m", "c", {})
    assert "scanner" not in snapshot.sections


def test_combine_rejects_different_lineage_and_accepts_matching_lineage() -> None:
    first = PresentationSnapshot("t", "m", "c", {"scanner": {}})
    second = PresentationSnapshot("t", "m", "c", {"portfolio": {}})
    combined = combine_snapshots(first, second)
    assert set(combined.sections) == {"scanner", "portfolio"}

    different_time = PresentationSnapshot("later", "m", "c", {"stress": {}})
    try:
        combine_snapshots(first, different_time)
    except ValueError as exc:
        assert "different lineage" in str(exc)
    else:
        raise AssertionError("different snapshot times must not be combined")
