"""Run a bounded authenticated collector smoke without persisting credentials."""

from __future__ import annotations

import argparse
import asyncio
import getpass
from dataclasses import replace
from pathlib import Path

from btc_option_scanner.collector import CollectorSettings, run_collector
from btc_option_scanner.deribit import DeribitCredentials


async def smoke(args: argparse.Namespace, credentials: DeribitCredentials) -> int:
    settings = replace(
        CollectorSettings.from_environment(),
        database_path=args.database,
        status_path=args.status,
        websocket_url=args.url,
        book_interval=args.book_interval,
        max_instruments=args.max_instruments,
        max_book_instruments=args.max_book_instruments,
        status_interval_seconds=1,
        retention_check_seconds=5,
        minimum_free_bytes=1,
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        run_collector(settings, stop_event=stop_event, credentials=credentials)
    )
    await asyncio.sleep(args.duration_seconds)
    stop_event.set()
    return await task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, default=20)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument(
        "--url",
        choices=(
            "wss://www.deribit.com/ws/api/v2",
            "wss://test.deribit.com/ws/api/v2",
        ),
        default="wss://www.deribit.com/ws/api/v2",
    )
    parser.add_argument("--book-interval", choices=("100ms", "raw"), default="raw")
    parser.add_argument("--max-instruments", type=int, default=3)
    parser.add_argument("--max-book-instruments", type=int, default=1)
    args = parser.parse_args()
    client_id = input("Deribit client id: ")
    client_secret = getpass.getpass("Deribit client secret: ")
    credentials = DeribitCredentials(client_id, client_secret)
    return asyncio.run(smoke(args, credentials))


if __name__ == "__main__":
    raise SystemExit(main())
