from __future__ import annotations

import os

import pytest

from btc_option_scanner.deribit import DeribitPublicRestClient, RateLimitConfig


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_DERIBIT_LIVE_SMOKE") != "1",
    reason="set RUN_DERIBIT_LIVE_SMOKE=1 to allow public-network access",
)
def test_public_rest_smoke() -> None:
    client = DeribitPublicRestClient(
        config=RateLimitConfig(requests_per_second=1, timeout_seconds=10)
    )
    response = client.get_instruments("BTC")
    assert isinstance(response.get("result"), list)
