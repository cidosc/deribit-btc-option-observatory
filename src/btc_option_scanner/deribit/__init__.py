"""Read-only Deribit transport and canonicalization boundary."""

from .auth import (
    AuthenticationError,
    DeribitCredentials,
    ReadOnlyWebSocketAuthenticator,
    validate_read_only_scope,
)
from .book import BookSequenceTracker, BookUpdateResult
from .ingestion import PublicDataSpine, TickerObservation
from .lifecycle import InstrumentLifecycle
from .parser import NormalizationError, normalize_instrument, normalize_ticker, record_raw_event
from .replay import EventReplayer
from .rest import (
    DeribitPublicRestClient,
    RateLimitConfig,
    rest_base_url_for_websocket,
)
from .websocket import (
    BookSubscription,
    PublicWebSocketConnection,
    ReconnectingTickerStream,
    TickerSubscription,
    connect_public_websocket,
)

__all__ = [
    "DeribitPublicRestClient",
    "AuthenticationError",
    "DeribitCredentials",
    "ReadOnlyWebSocketAuthenticator",
    "validate_read_only_scope",
    "BookSequenceTracker",
    "BookUpdateResult",
    "RateLimitConfig",
    "rest_base_url_for_websocket",
    "InstrumentLifecycle",
    "PublicDataSpine",
    "TickerObservation",
    "NormalizationError",
    "record_raw_event",
    "normalize_instrument",
    "normalize_ticker",
    "ReconnectingTickerStream",
    "TickerSubscription",
    "BookSubscription",
    "PublicWebSocketConnection",
    "connect_public_websocket",
    "EventReplayer",
]
