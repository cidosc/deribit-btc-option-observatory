# Ubuntu 与 systemd 部署

本文适用于 Ubuntu 24.04 以及其他使用 systemd 的现代 Linux 发行版。非 Debian 系统请
替换相应的软件包名称。

## 1. 准备主机

使用独立的无特权账号。systemd unit 不授予 capability，只允许进程写入 systemd 管理的
状态、运行时和可选日志目录。

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

如果仓库是私有仓库，应给 `btc-option` 账号配置只读 deploy key；不要把个人且可写的
GitHub 私钥复制到服务器。

## 2. 安装 Python 环境

```bash
cd /opt/deribit-btc-option-observatory
sudo -u btc-option python3 -m venv .venv
sudo -u btc-option .venv/bin/python -m pip install --upgrade pip
sudo -u btc-option .venv/bin/python -m pip install \
  -c deploy/constraints-collector.txt '.[collector]'
```

需要在服务器运行验收测试时安装 `'.[dev,collector]'`，然后执行：

```bash
sudo -u btc-option .venv/bin/python -m pytest -q
sudo -u btc-option .venv/bin/python -m ruff check .
```

## 3. 配置和可选只读认证

```bash
sudo install -d -o root -g root -m 0755 /etc/btc-option-observatory
sudo install -o root -g root -m 0600 deploy/collector.env.example \
  /etc/btc-option-observatory/collector.env
sudoedit /etc/btc-option-observatory/collector.env
```

公开采集不需要密钥。需要认证行情时，在这个仅 root 可读的文件中同时加入
`DERIBIT_CLIENT_ID` 和 `DERIBIT_CLIENT_SECRET`。Deribit key 不得有写权限；服务器有
固定 IP 时应设置 IP allowlist。程序会检查 Deribit 实际返回的 scope，并拒绝任何
`:read_write` 或未知权限。

Testnet 凭据必须配合 `wss://test.deribit.com/ws/api/v2`，不能与 Mainnet 混用。先确认
认证下的 `100ms` 采集稳定，再按需将 `BTC_OPTION_BOOK_INTERVAL` 设置为 `raw`。

systemd EnvironmentFile 不执行 shell 变量展开，不要在值后添加行内注释。

## 4. 安装和启动服务

```bash
sudo install -o root -g root -m 0644 \
  deploy/systemd/btc-option-observatory.service \
  /etc/systemd/system/btc-option-observatory.service
sudo systemctl daemon-reload
sudo systemctl enable --now btc-option-observatory.service
```

检查启动、行情新鲜度、持久化和是否发生重启循环：

```bash
sudo systemctl status btc-option-observatory.service --no-pager
sudo journalctl -u btc-option-observatory.service -n 100 --no-pager
sudo -u btc-option /opt/deribit-btc-option-observatory/.venv/bin/btc-option-healthcheck \
  --status /run/btc-option-observatory/collector-status.json
sudo sqlite3 /var/lib/btc-option-observatory/collector.sqlite \
  'PRAGMA integrity_check; PRAGMA journal_mode;'
```

SQLite 正常输出应包含 `ok` 和 `wal`。网络重连期间健康检查可能短暂失败，持续失败则需要
调查。

## 5. 日志、保留期和容量

默认日志进入 journald：

```bash
sudo journalctl -u btc-option-observatory.service -f
sudo journalctl --disk-usage
```

请按服务器策略配置 journald 全局保留期。如果启用了 `BTC_OPTION_LOG_FILE`，再安装可选的
logrotate 配置：

```bash
sudo install -o root -g root -m 0644 deploy/logrotate/btc-option-observatory \
  /etc/logrotate.d/btc-option-observatory
```

`BTC_OPTION_RETENTION_DAYS` 控制在线证据清理。派生实体引用的父快照受到保护，每个合约
最新一版规格也会保留。删除后的 SQLite 空闲页会复用；常驻进程不会执行可能长时间阻塞的
`VACUUM`。状态文件会报告数据库、WAL、SHM 的总字节数。

当空闲磁盘低于 `BTC_OPTION_MIN_FREE_BYTES` 时，服务以 78 退出，并且 systemd 不会自动
重启。扩容或安全归档后再人工启动。保留期不等于备份，在线一致性备份应使用：

```bash
sudo install -d -o btc-option -g btc-option -m 0700 /var/backups/btc-option-observatory
sudo -u btc-option sqlite3 /var/lib/btc-option-observatory/collector.sqlite \
  ".backup '/var/backups/btc-option-observatory/collector-$(date -u +%Y%m%dT%H%M%SZ).sqlite'"
```

备份应加密并复制到其他主机或对象存储。

## 6. 升级和回滚

先备份数据库，再执行快进更新和检查：

```bash
sudo systemctl stop btc-option-observatory.service
cd /opt/deribit-btc-option-observatory
sudo -u btc-option git pull --ff-only
sudo -u btc-option .venv/bin/python -m pip install \
  -c deploy/constraints-collector.txt '.[collector]'
sudo -u btc-option .venv/bin/python -m pytest -q
sudo systemctl start btc-option-observatory.service
```

回滚应部署此前通过验证的 tag 或 release。只有数据库迁移确实不兼容时才恢复备份。不要用
普通 `cp` 复制正在运行的 SQLite 文件，应使用 SQLite `.backup`。

## 7. 服务器验收标准

至少观察 24 小时，最好跨过一次期权到期事件，再判断主机稳定：

- 没有重复重启循环；
- 常规重连期间状态文件持续更新；
- sequence gap 会触发重连并取得新 snapshot；
- 数据库增长符合磁盘容量和 retention 设置；
- catalog 变化会更新受限的订阅集合；
- SIGTERM 在 30 秒内退出，SQLite `integrity_check` 仍为 `ok`；
- journal、状态文件、数据库和备份中均没有凭据。

本服务始终只用于研究。若以后需要远程访问 Streamlit，必须在前方增加 TLS 和身份认证，
不要把 Streamlit 端口直接暴露到公网。
