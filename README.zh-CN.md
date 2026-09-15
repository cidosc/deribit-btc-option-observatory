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

默认 `scanner-lite` 是低存储模式：订阅 60 个聚合 ticker，每个合约最多每 60 秒持久化一
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

daemon 现在会对时间一致且新鲜的 WebSocket snapshot 运行既有规则 scanner，并默认原子写入
`runtime/latest-recommendation.json`。文件包含供人工复核的 Top 候选、真实 Ask、盘口数量、
Delta、DTE、spread、entry DCE、理由、风险以及完整 snapshot/metric/run ID。时间一致的 peer
样本不足时会失败关闭，不会发送候选。程序不会下单。

实时提醒默认采用蓝图中的中长期 ITM 假设：绝对 Delta `0.80–0.90`、相对价差不超过 `5%`，
并至少有 3 个可比较合约。这样可阻止低 Delta 的便宜 OTM Call 仅仅因为
`DCE = Delta / Ask` 中 Ask 很小而排到前面。这些都是可通过环境变量修改的实验门槛，不代表
盈利保证。

可选机器人提醒优先使用飞书群自定义机器人的 HTTPS Webhook：

```bash
export BTC_OPTION_FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/REPLACE_ME
# 在飞书机器人中启用签名校验时建议配置：
export BTC_OPTION_FEISHU_SIGNING_SECRET=...
```

提醒使用纯文本。即使排名集合发生变化，也必须满足默认 30 分钟的硬最小间隔；集合未变化时
默认 6 小时才重复。可用 `BTC_OPTION_ALERT_MIN_INTERVAL_SECONDS` 和
`BTC_OPTION_ALERT_REMINDER_SECONDS` 调整。程序只接受飞书/Lark 官方 HTTPS bot hook 域名。
发送失败不终止 collector，错误会写入状态文件。本阶段不接 Telegram，也不再要求部署
QQ/OneBot 运行时。

飞书 WebSocket 长连接的用途是让企业自建应用接收飞书事件和回调，单向候选通知不需要它。
以后若增加交互式机器人命令，应把事件接收进程与只读市场 collector 分开实现。

容量规划上，默认 60 合约、60 秒持久化，并保存实时指标和扫描 lineage 时，建议按
**0.5–1.5 GiB/日**预留。这是工程估算而非固定配额；目标机运行数小时后应以状态文件中的
`estimated_database_growth_bytes_per_day` 为准。限定具体合约或延长持久化间隔可近似线性
降低用量；raw book 仍可能达到每日数十 GiB，所以保持按需开启。

活动 SQLite、WAL 和 SHM 应放在本地 ext4/XFS 等块文件系统。EBS、云硬盘等块设备可以使用；
不要把活动数据库直接放到 OneDrive、Dropbox、rclone/FUSE、SMB 或对象存储上，这些系统的
锁和同步语义可能导致 SQLite 卡顿或损坏。应把一致性备份或压缩归档上传到云盘，具体方式见
Ubuntu 部署文档。

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
