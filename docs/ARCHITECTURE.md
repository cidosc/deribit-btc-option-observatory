# Architecture

The implementation is a public-data, research-only pipeline:

`Deribit public payload -> raw event -> normalized MarketSnapshot -> metrics -> scanner / portfolio -> roll / stress -> PresentationSnapshot -> CLI or dashboard`

`MarketSnapshot` is immutable and carries raw-event IDs, clocks, executable bid/ask states, mark, index, IV, Greeks and quality flags. Metrics are built from a specific market snapshot. Scanner, portfolio, roll and stress operate on supplied DTOs; they do not fetch a current quote. A CLI reads a precomputed `PresentationSnapshot` JSON only and does not contact Deribit or a database.

Prices remain side-specific: new long entry uses Ask, long liquidation and roll sale use Bid, and Mark is descriptive/mark-NAV only. Stress is a conditional model reprice, not executable pricing. No module is an order router.

SQLite stores raw evidence, normalized snapshots, and canonical derived evidence rows with parent IDs. CLI and Dashboard consume one lineage-checked `PresentationSnapshot`; they do not recompute business logic.
