"""Probe Deribit OAuth without printing or persisting credentials or access tokens."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os

from btc_option_scanner.deribit import (
    AuthenticationError,
    DeribitCredentials,
    ReadOnlyWebSocketAuthenticator,
    connect_public_websocket,
)


async def probe(url: str, scope: str, client_id: str, client_secret: str) -> dict:
    connection = await connect_public_websocket(url)
    try:
        authentication = await ReadOnlyWebSocketAuthenticator(
            DeribitCredentials(client_id, client_secret), requested_scope=scope
        )(connection)
        return {"status": "AUTHENTICATED_READ_ONLY", **authentication}
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        choices=(
            "wss://www.deribit.com/ws/api/v2",
            "wss://test.deribit.com/ws/api/v2",
        ),
        default="wss://test.deribit.com/ws/api/v2",
    )
    parser.add_argument(
        "--scope",
        default="connection account:none trade:none wallet:none",
    )
    args = parser.parse_args()
    client_id = os.getenv("DERIBIT_CLIENT_ID") or input("Deribit client id: ")
    client_secret = os.getenv("DERIBIT_CLIENT_SECRET") or getpass.getpass(
        "Deribit client secret: "
    )
    try:
        result = asyncio.run(probe(args.url, args.scope, client_id, client_secret))
    except AuthenticationError as exc:
        print(json.dumps({"status": "AUTHENTICATION_FAILED", "reason": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
