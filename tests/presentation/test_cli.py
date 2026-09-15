from __future__ import annotations

import json

from btc_option_scanner.application import PresentationSnapshot, save_presentation_snapshot
from btc_option_scanner.cli import main


def test_cli_sections_share_one_snapshot_and_preserve_rejection(tmp_path, capsys) -> None:
    path = tmp_path / "snapshot.json"
    save_presentation_snapshot(
        PresentationSnapshot(
            "2026-09-15T00:00:00+00:00",
            "model@1",
            "config@1",
            {
                "scanner": {
                    "candidates": [
                        {
                            "instrument_name": "BTC-X-C",
                            "state": "NO_EXECUTABLE_ASK",
                            "rejected_reasons": ["NO_EXECUTABLE_ASK"],
                        }
                    ]
                }
            },
        ),
        path,
    )
    assert main(["--snapshot", str(path), "scan"]) == 0
    scanned = json.loads(capsys.readouterr().out)
    assert main(["--snapshot", str(path), "inspect", "BTC-X-C"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert scanned["metadata"] == inspected["metadata"]
    assert inspected["result"]["records"][0]["rejected_reasons"] == ["NO_EXECUTABLE_ASK"]


def test_cli_empty_section_is_explained(tmp_path, capsys) -> None:
    path = tmp_path / "snapshot.json"
    save_presentation_snapshot(PresentationSnapshot("t", "m", "c", {}), path)
    assert main(["--snapshot", str(path), "stress"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["status"] == "NO_DATA"
