# Ubuntu and systemd deployment

This guide deploys the read-only collector on Ubuntu 24.04 or another modern
systemd-based Linux distribution. Adapt package names for non-Debian systems.

## 1. Host preparation

Use a dedicated unprivileged account. The service unit grants no capabilities and only
writes under its systemd-managed state, runtime, and optional log directories.

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
```

For a private repository, configure a read-only deploy key for the `btc-option` account.
Do not copy a personal write-capable GitHub key to the server.

## 2. Python environment

```bash
cd /opt/deribit-btc-option-observatory
sudo -u btc-option python3 -m venv .venv
sudo -u btc-option .venv/bin/python -m pip install --upgrade pip
sudo -u btc-option .venv/bin/python -m pip install \
  -c deploy/constraints-collector.txt '.[collector]'
```

Install `.[dev,collector]` instead of `.[collector]` if the host must run acceptance tests;
production hosts normally do not need pytest or ruff.

## 3. Configuration and optional authentication

```bash
sudo install -d -o root -g root -m 0755 /etc/btc-option-observatory
sudo install -o root -g root -m 0600 deploy/collector.env.example \
  /etc/btc-option-observatory/collector.env
sudoedit /etc/btc-option-observatory/collector.env
```

Public-only collection requires no key. For authenticated market-data collection, add
both `DERIBIT_CLIENT_ID` and `DERIBIT_CLIENT_SECRET` to that root-readable file. Create a
Deribit key with no write authority, request the narrow default scope, and use an IP
allowlist when the server has a stable address. The collector validates the effective
scope returned by Deribit and refuses `:read_write` or unknown permission tokens.

Use `wss://test.deribit.com/ws/api/v2` with Testnet credentials. Mainnet and Testnet keys
are not interchangeable. Set `BTC_OPTION_BOOK_INTERVAL=raw` only after authenticated
collection works with `100ms`.

The environment file contains no shell expansion. Avoid inline comments after values.

## 4. Install the service

```bash
sudo install -o root -g root -m 0644 \
  deploy/systemd/btc-option-observatory.service \
  /etc/systemd/system/btc-option-observatory.service
sudo systemctl daemon-reload
sudo systemctl enable --now btc-option-observatory.service
```

Verify startup, stream freshness, persistence, and the absence of restart loops:

```bash
sudo systemctl status btc-option-observatory.service --no-pager
sudo journalctl -u btc-option-observatory.service -n 100 --no-pager
sudo -u btc-option /opt/deribit-btc-option-observatory/.venv/bin/btc-option-healthcheck \
  --status /run/btc-option-observatory/collector-status.json
sudo sqlite3 /var/lib/btc-option-observatory/collector.sqlite \
  'PRAGMA integrity_check; PRAGMA journal_mode;'
```

Expected SQLite output includes `ok` and `wal`. A health check may briefly fail during a
network reconnect; persistent failure requires investigation.

## 5. Logging, retention, and capacity

The default unit logs to journald:

```bash
sudo journalctl -u btc-option-observatory.service -f
sudo journalctl --disk-usage
```

Configure journald retention globally according to the host policy. If file logging is
enabled with `BTC_OPTION_LOG_FILE`, install the provided optional logrotate policy.

```bash
sudo install -o root -g root -m 0644 deploy/logrotate/btc-option-observatory \
  /etc/logrotate.d/btc-option-observatory
```

`BTC_OPTION_RETENTION_DAYS` applies online evidence deletion. Derived-entity parent
snapshots are protected, and the newest instrument specification is kept. SQLite free
pages are reused; the daemon intentionally does not run a blocking `VACUUM`. Database,
WAL, and SHM bytes are reported in the status file.

Keep `BTC_OPTION_COLLECTION_PROFILE=scanner-lite` for ordinary operation. Order books
are disabled in this profile and ticker persistence is sampled. Enable `evidence` or a
nonzero `BTC_OPTION_MAX_BOOK_INSTRUMENTS` only for bounded investigations, then observe
`estimated_database_growth_bytes_per_day` before extending the run.

Live candidates are written to `/run/btc-option-observatory/latest-recommendation.json`.
Each recommendation cycle actively refreshes a bounded set of core instruments through the
public `public/ticker` REST endpoint of the same Deribit environment as the configured
WebSocket; refresh knobs (`BTC_OPTION_REFRESH_LIMIT`, `BTC_OPTION_REFRESH_REQUESTS_PER_SECOND`,
`BTC_OPTION_REFRESH_TIMEOUT_SECONDS`, `BTC_OPTION_REFRESH_CYCLE_BUDGET_SECONDS`,
`BTC_OPTION_REFRESH_MAX_CONCURRENCY`) and the resulting write volume are visible under
`recommendation.refresh` in the status file. When a round is stale, skewed, timed out,
failed, or below the cohort minimum, the previous candidate file is atomically replaced
with an empty fail-closed state.
To send them through a Feishu custom bot, set `BTC_OPTION_FEISHU_WEBHOOK_URL` and,
when bot signature verification is enabled, `BTC_OPTION_FEISHU_SIGNING_SECRET` in the
protected environment file, then restart the service. These values stay in that root-owned
`0600` file and are never written to the recommendation JSON or logs. The webhook URL is
itself a secret. Feishu's WebSocket long connection receives app events; it is not required
for this one-way notification path.

The shipped alert defaults are a rolling 120–240 DTE interval, Delta 0.80–0.90, relative
spread no more than 5%, a three-contract peer cohort, a hard 30-minute minimum notification
interval, and a six-hour unchanged reminder. The DTE bounds come from
`universe.min_dte_days` and `universe.max_dte_days` in `config.yaml`; they do not pin one
expiry. Keep these defaults for the first deployment; tune only after reviewing rejection
reasons and retained history.

The service exits with code 78 and systemd does not restart it when free disk falls below
`BTC_OPTION_MIN_FREE_BYTES`. Increase capacity or safely archive data before restarting.
Retention is not a backup. For a consistent online backup:

```bash
sudo install -d -o btc-option -g btc-option -m 0700 /var/backups/btc-option-observatory
sudo -u btc-option sqlite3 /var/lib/btc-option-observatory/collector.sqlite \
  ".backup '/var/backups/btc-option-observatory/collector-$(date -u +%Y%m%dT%H%M%SZ).sqlite'"
```

Encrypt and copy backups off-host according to your own retention policy. The active
database, `-wal`, and `-shm` files must remain together on a local Linux block filesystem;
a cloud VM block volume is acceptable. Consumer sync drives, rclone/FUSE, SMB/NFS shares,
and object-storage mounts are not supported as the live SQLite path. Mount cloud storage
only for completed backup/archive output. Never copy only the database file while the
service is writing.

## 6. Upgrade and rollback

Back up the database first, then perform a fast-forward update and rerun checks:

```bash
sudo systemctl stop btc-option-observatory.service
cd /opt/deribit-btc-option-observatory
sudo -u btc-option git pull --ff-only
sudo -u btc-option .venv/bin/python -m pip install \
  -c deploy/constraints-collector.txt '.[collector]'
sudo -u btc-option .venv/bin/python -m pytest -q
sudo systemctl start btc-option-observatory.service
```

Rollback by deploying a previously tested tag or release checkout and restoring a backup
only if a migration made the database incompatible. Never copy a live SQLite file with
plain `cp`; use the SQLite backup command.

## 7. Operational acceptance

Before calling a host stable, observe at least 24 hours and preferably an option expiry:

- no repeated restart loop;
- health status remains fresh through ordinary reconnects;
- sequence gaps cause reconnect and a new snapshot;
- database growth matches available disk and retention settings;
- catalog changes update the bounded subscription set;
- SIGTERM completes within 30 seconds and SQLite integrity remains `ok`;
- no credentials appear in journal, status JSON, database, or backups.

The service remains research-only. Do not expose Streamlit directly to the internet;
place it behind authentication and TLS if remote dashboard access is later required.
