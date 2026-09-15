"""Read-only CLI over precomputed application presentation snapshots.

No command opens a database, contacts Deribit, or invokes a calculation
service.  This intentionally prevents accidental mixing of live values with a
saved ScannerRun/PortfolioSnapshot/RollEvaluation.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .application import load_presentation_snapshot


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _section(snapshot: Any, name: str) -> dict[str, Any]:
    value = snapshot.sections.get(name)
    if value is None:
        return {
            "status": "NO_DATA",
            "message": f"No {name.replace('_', ' ')} application output exists in this snapshot.",
        }
    return value


def _inspect(snapshot: Any, instrument: str) -> dict[str, Any]:
    scanner = snapshot.sections.get("scanner", {})
    candidates = scanner.get("candidates", []) if isinstance(scanner, dict) else []
    matches = [row for row in candidates if row.get("instrument_name") == instrument]
    if not matches:
        return {"status": "NO_DATA", "message": f"No display record for {instrument}."}
    return {"snapshot_time": snapshot.snapshot_time, "records": matches}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only BTC option research presentation")
    parser.add_argument("--snapshot", required=True, help="precomputed presentation snapshot JSON")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="show scanner application output")
    inspect = sub.add_parser("inspect", help="show one scanner candidate and explanations")
    inspect.add_argument("instrument")
    sub.add_parser("roll", help="show recorded roll evaluations")
    sub.add_parser("portfolio", help="show recorded portfolio valuation")
    sub.add_parser("stress", help="show recorded conditional stress results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        snapshot = load_presentation_snapshot(args.snapshot)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot load presentation snapshot: {exc}", file=sys.stderr)
        return 2
    metadata = {
        "snapshot_time": snapshot.snapshot_time,
        "model_version": snapshot.model_version,
        "config_version": snapshot.config_version,
        "price_basis": (
            "Entry uses executable Ask; liquidation uses executable Bid; mark is descriptive."
        ),
    }
    if args.command == "inspect":
        result = _inspect(snapshot, args.instrument)
    else:
        section_name = "scanner" if args.command == "scan" else args.command
        result = _section(snapshot, section_name)
    _emit({"metadata": metadata, "result": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
