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

Public-only operation is the default. For optional authenticated read-only collection,
set both `DERIBIT_CLIENT_ID` and `DERIBIT_CLIENT_SECRET` in the process environment.
Use the default scope `connection account:none trade:none wallet:none`. Authenticated
`raw` order book collection is opt-in with `BTC_OPTION_BOOK_INTERVAL=raw`.

Never commit credentials or give this service a key containing `:read_write` permissions.
The collector does not load `.env` automatically.

## Read-only interfaces

The CLI and dashboard consume an immutable, precomputed presentation snapshot. They do
not query Deribit or recompute core financial logic.

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
