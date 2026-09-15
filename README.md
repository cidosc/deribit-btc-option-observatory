# Deribit BTC Option Observatory

[简体中文](README.zh-CN.md)

A read-only research system for collecting Deribit BTC inverse-option market data,
scanning executable Ask/Bid opportunities, valuing hypothetical portfolios, comparing
rolls, and replaying historical evidence. It does **not** place, edit, or cancel orders.

## Safety boundary

- New long entries use Ask, exits use Bid, and Mark remains theory-only.
- Stale, out-of-order, sequence-gap, unknown-unit, and missing-side data fail closed.
- Optional Deribit credentials are used only to authenticate the market-data connection.
- Effective OAuth scopes are checked at runtime; write or unknown authority is refused.
- No trading, transfer, withdrawal, or private write method exists in the source tree.

## Requirements

- Python 3.12+
- SQLite 3
- Network access to Deribit only when running live collectors

Development setup:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,collector,dashboard]'
python -m pytest
python -m ruff check .
```

On Windows PowerShell, activate with `.\.venv\Scripts\Activate.ps1` and use double
quotes around extras.

## Ubuntu/systemd quick deployment

Ubuntu 24.04 and other modern systemd Linux distributions can install the collector as
an unprivileged service:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv sqlite3 ca-certificates
sudo useradd --system --user-group --create-home --home-dir /var/lib/btc-option-observatory \
  --shell /usr/sbin/nologin btc-option
sudo install -d -o btc-option -g btc-option -m 0750 \
  /opt/deribit-btc-option-observatory
sudo -u btc-option git clone \
  git@github.com:cidosc/deribit-btc-option-observatory.git \
  /opt/deribit-btc-option-observatory
cd /opt/deribit-btc-option-observatory
sudo -u btc-option python3 -m venv .venv
sudo -u btc-option .venv/bin/python -m pip install --upgrade pip
sudo -u btc-option .venv/bin/python -m pip install \
  -c deploy/constraints-collector.txt '.[collector]'
sudo install -d -o root -g root -m 0755 /etc/btc-option-observatory
sudo install -o root -g root -m 0600 deploy/collector.env.example \
  /etc/btc-option-observatory/collector.env
sudo install -o root -g root -m 0644 \
  deploy/systemd/btc-option-observatory.service \
  /etc/systemd/system/btc-option-observatory.service
sudo systemctl daemon-reload
sudo systemctl enable --now btc-option-observatory.service
```

If the repository is private, install a read-only GitHub deploy key for the service
account before cloning. Edit `/etc/btc-option-observatory/collector.env` to change
retention, channel limits, endpoints, or optional Deribit credentials. Then verify:

```bash
sudo systemctl status btc-option-observatory.service --no-pager
sudo journalctl -u btc-option-observatory.service -n 100 --no-pager
sudo -u btc-option /opt/deribit-btc-option-observatory/.venv/bin/btc-option-healthcheck \
  --status /run/btc-option-observatory/collector-status.json
sudo sqlite3 /var/lib/btc-option-observatory/collector.sqlite \
  'PRAGMA integrity_check; PRAGMA journal_mode;'
```

Expected SQLite results are `ok` and `wal`. See the full deployment guide for backups,
upgrades, log policy, retention semantics, and server acceptance checks.

## Long-running collector

The installed `btc-option-collector` command runs until SIGINT or SIGTERM. It performs
catalog reconciliation, bounded ticker/book subscriptions, reconnect/resubscribe,
book sequence fail-closure, SQLite WAL persistence, retention, disk-floor checks, and
atomic health-status updates.

The default `scanner-lite` profile is intentionally low-volume: 60 aggregated ticker
subscriptions, at most one persisted ticker per instrument every 60 seconds, and no
order-book subscription. Best Bid/Ask, sizes, IV, Greeks, volume, and open interest from
ticker messages remain available as scanner inputs.

```bash
export BTC_OPTION_SCANNER_CONFIG="$PWD/config.yaml"
export BTC_OPTION_DB_PATH="$PWD/data/collector.sqlite"
export BTC_OPTION_STATUS_PATH="$PWD/runtime/collector-status.json"
btc-option-collector
```

Check it from another shell:

```bash
btc-option-healthcheck --status runtime/collector-status.json
```

High-volume evidence collection is opt-in:

```bash
export BTC_OPTION_COLLECTION_PROFILE=evidence
# Optional authenticated unaggregated book; can grow very quickly:
export BTC_OPTION_BOOK_INTERVAL=raw
btc-option-collector
```

`evidence` defaults to 180 tickers, 20 books, and persistence of every ticker update.
Individual settings can instead be overridden with `BTC_OPTION_MAX_INSTRUMENTS`,
`BTC_OPTION_MAX_BOOK_INSTRUMENTS`, `BTC_OPTION_TICKER_PERSIST_INTERVAL_SECONDS`, and
`BTC_OPTION_INSTRUMENTS`. A nonzero book count is always high-volume because sequence
validation requires every received increment. Monitor
`estimated_database_growth_bytes_per_day` in the status JSON before choosing retention.

Public-only operation is the default. For optional authenticated read-only collection,
set both `DERIBIT_CLIENT_ID` and `DERIBIT_CLIENT_SECRET` in the process environment.
Use the default scope `connection account:none trade:none wallet:none`. Authenticated
`raw` order book collection is opt-in with `BTC_OPTION_BOOK_INTERVAL=raw`.

Never commit credentials or give this service a key containing `:read_write` permissions.
The collector does not load `.env` automatically.

## Read-only interfaces

The CLI and dashboard consume an immutable, precomputed presentation snapshot. They do
not query Deribit or recompute core financial logic.

The daemon runs the existing rule scanner on coherent, fresh WebSocket snapshots and
atomically writes `runtime/latest-recommendation.json` by default. The file contains the
top manual-review candidates, executable Ask, quote size, Delta, DTE, spread, entry DCE,
reasons, risks, and complete snapshot/metric/run IDs. If the coherent cohort is too small,
the status fails closed instead of sending a candidate. Nothing places an order.

Optional robot delivery uses the OneBot v11 forward WebSocket API:

```bash
export BTC_OPTION_ONEBOT_WS_URL=ws://127.0.0.1:3001/api
export BTC_OPTION_ONEBOT_TARGET_TYPE=group  # or private
export BTC_OPTION_ONEBOT_TARGET_ID=123456789
# export BTC_OPTION_ONEBOT_ACCESS_TOKEN=...
```

All three non-token fields are required together. Alerts are plain text, deduplicated when
the ranked instrument set is unchanged, and repeated after the configured reminder period.
An alert failure is non-fatal and appears in collector status. Telegram is not included.

For capacity planning, the default 60-symbol/60-second profile is expected to be roughly
**0.5–1.5 GiB/day** when all symbols update and live metric/run lineage is retained. This is
an engineering range, not a quota: use the status field
`estimated_database_growth_bytes_per_day` after several hours on the target host. An exact
instrument list or longer persistence interval reduces it linearly. Raw books can still
reach tens of GiB/day and remain opt-in.

Keep the live SQLite database and its WAL on a local block filesystem such as ext4/XFS.
Cloud block volumes such as EBS or Persistent Disk are suitable. Do not put the active WAL
database on OneDrive/Dropbox/rclone/FUSE, SMB, or ordinary object storage: sync semantics
and file locking can corrupt or stall SQLite. Upload consistent SQLite backup files or
compressed archives to cloud storage instead; see the Ubuntu deployment guide.

```bash
python -m btc_option_scanner.cli --snapshot snapshot.json scan
python -m btc_option_scanner.cli --snapshot snapshot.json portfolio
python -m btc_option_scanner.cli --snapshot snapshot.json roll
python -m btc_option_scanner.cli --snapshot snapshot.json stress
python -m btc_option_scanner.cli --snapshot snapshot.json inspect BTC-25DEC26-20000-C

streamlit run src/btc_option_scanner/dashboard/app.py -- --snapshot snapshot.json
```

## Documentation

- [Ubuntu/Linux deployment](docs/DEPLOYMENT_UBUNTU.md)
- [Ubuntu/Linux 部署（中文）](docs/DEPLOYMENT_UBUNTU.zh-CN.md)
- [Operations runbook](docs/RUNBOOK.md)
- [Domain contract](docs/DOMAIN_CONTRACT.md)
- [Acceptance report](docs/ACCEPTANCE_REPORT.md)

This repository is suitable for supervised long-running research collection after the
documented host checks. It is not a production trading system, and the present evidence
does not establish strategy profitability or production-grade parameter calibration.
