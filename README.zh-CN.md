# Deribit BTC 期权观测站

[English](README.md)

这是一个只读研究系统，用于采集 Deribit BTC 币本位期权行情、按照真实 Ask/Bid
评估候选、估值假想组合、比较滚仓方案并回放历史证据。系统**不能**下单、改单或撤单。

## 安全边界

- 新增长仓只使用 Ask，退出只使用 Bid，Mark 只用于理论估值。
- 数据 stale、乱序、book sequence gap、量纲未验证或盘口缺边时失败关闭。
- Deribit 凭据是可选项，只用于认证只读行情连接。
- 程序会检查服务端实际返回的 OAuth scope；任何写权限或未知权限都会被拒绝。
- 源码中没有交易、转账、提现或私有写接口。

## 环境要求

- Python 3.12+
- SQLite 3
- 只有运行 live collector 时才需要访问 Deribit 网络

开发环境安装：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,collector,dashboard]'
python -m pytest
python -m ruff check .
```

Windows PowerShell 使用 `.\.venv\Scripts\Activate.ps1` 激活环境，并对 extras 使用双引号。

## Ubuntu/systemd 快速部署

Ubuntu 24.04 以及其他现代 systemd Linux 发行版可以按以下方式安装无特权常驻服务：

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

私有仓库需要在 clone 前为服务账号配置只读 GitHub deploy key。修改
`/etc/btc-option-observatory/collector.env` 可以调整保留期、订阅上限、Deribit 环境和
可选凭据。启动后执行：

```bash
sudo systemctl status btc-option-observatory.service --no-pager
sudo journalctl -u btc-option-observatory.service -n 100 --no-pager
sudo -u btc-option /opt/deribit-btc-option-observatory/.venv/bin/btc-option-healthcheck \
  --status /run/btc-option-observatory/collector-status.json
sudo sqlite3 /var/lib/btc-option-observatory/collector.sqlite \
  'PRAGMA integrity_check; PRAGMA journal_mode;'
```

SQLite 预期输出为 `ok` 和 `wal`。备份、升级、日志、保留策略和服务器验收步骤见完整部署文档。

## 常驻采集器

安装后运行 `btc-option-collector`。进程会持续运行到收到 SIGINT 或 SIGTERM，并负责：

- 定期同步 instrument catalog；
- 有上限的 ticker/book 订阅；
- 断线重连和重新订阅；
- book sequence gap 失败关闭并重新获取快照；
- SQLite WAL 持久化和保留期清理；
- 最低磁盘余量保护；
- 原子写入健康状态文件。

默认 `scanner-lite` 是低存储模式：订阅 60 个聚合 ticker，每个合约最多每 20 秒持久化一
条 ticker，不订阅 order book。ticker 中的 best Bid/Ask、两边数量、IV、Greeks、成交量和
持仓量仍可作为扫描输入。

```bash
export BTC_OPTION_SCANNER_CONFIG="$PWD/config.yaml"
export BTC_OPTION_DB_PATH="$PWD/data/collector.sqlite"
export BTC_OPTION_STATUS_PATH="$PWD/runtime/collector-status.json"
btc-option-collector
```

另开终端检查：

```bash
btc-option-healthcheck --status runtime/collector-status.json
```

高流量证据采集必须显式开启：

```bash
export BTC_OPTION_COLLECTION_PROFILE=evidence
# 可选的认证未聚合 book，数据增长会非常快：
export BTC_OPTION_BOOK_INTERVAL=raw
btc-option-collector
```

`evidence` 默认订阅 180 个 ticker、20 个 book，并保存每次 ticker 更新。也可分别设置
`BTC_OPTION_MAX_INSTRUMENTS`、`BTC_OPTION_MAX_BOOK_INSTRUMENTS`、
`BTC_OPTION_TICKER_PERSIST_INTERVAL_SECONDS` 和 `BTC_OPTION_INSTRUMENTS`。book 数量只要
不为 0 就属于高流量模式，因为 sequence 校验要求连续保存每个收到的增量。设置 retention
前请观察状态 JSON 中的 `estimated_database_growth_bytes_per_day`。

默认不需要 API key。需要认证只读采集时，通过进程环境同时设置
`DERIBIT_CLIENT_ID` 和 `DERIBIT_CLIENT_SECRET`，默认 scope 为
`connection account:none trade:none wallet:none`。只有认证模式才能把
`BTC_OPTION_BOOK_INTERVAL` 改成 `raw`。

不要提交真实凭据，也不要给本服务配置任何包含 `:read_write` 的权限。
程序不会自动读取 `.env` 文件。

## 只读界面

CLI 和 Dashboard 只读取不可变的 presentation snapshot，不访问 Deribit，
也不会重新计算核心金融逻辑。

重要边界：daemon 当前负责采集和规范化扫描输入，但尚未定时执行 scanner，也不会生成实时
候选或提醒文件。CLI 和 Dashboard 仍需要预计算的 presentation snapshot。任何 profile 都
不会下单。

```bash
python -m btc_option_scanner.cli --snapshot snapshot.json scan
python -m btc_option_scanner.cli --snapshot snapshot.json portfolio
python -m btc_option_scanner.cli --snapshot snapshot.json roll
python -m btc_option_scanner.cli --snapshot snapshot.json stress
python -m btc_option_scanner.cli --snapshot snapshot.json inspect BTC-25DEC26-20000-C

streamlit run src/btc_option_scanner/dashboard/app.py -- --snapshot snapshot.json
```

## 文档

- [Ubuntu/Linux 部署说明](docs/DEPLOYMENT_UBUNTU.zh-CN.md)
- [Ubuntu/Linux deployment](docs/DEPLOYMENT_UBUNTU.md)
- [运行手册](docs/RUNBOOK.md)
- [领域契约](docs/DOMAIN_CONTRACT.md)
- [验收报告](docs/ACCEPTANCE_REPORT.md)

完成部署文档中的主机检查后，当前版本可用于有人监控的长期研究采集；它仍不是生产交易
系统，现有历史也不足以证明策略收益或生产参数有效性。
