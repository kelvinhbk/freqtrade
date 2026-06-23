# FreqAI v5 策略部署文档 — 腾讯云香港轻量服务器

## 概览

| 项目 | 内容 |
|------|------|
| 策略 | FreqaiExampleStrategy_v5（LightGBM + Coinglass 衍生品特征） |
| 交易对 | BTC/USDT:USDT、ETH/USDT:USDT |
| 时间框架 | 15 分钟 |
| 交易模式 | Binance USDT 永续合约（逐仓） |
| 每笔投入 | 200 USDT |
| 最大持仓 | 3 笔（实际受限于 2 个交易对） |

## 服务器要求

### 最低配置

| 资源 | 要求 | 说明 |
|------|------|------|
| CPU | 2 核 | FreqAI 训练需要计算力，4 核更佳 |
| 内存 | 4 GB | LightGBM 训练 + freqtrade 运行，8 GB 更佳 |
| 磁盘 | 40 GB SSD | 模型文件 + 数据 + 日志 |
| 系统 | Ubuntu 22.04 / 24.04 LTS | 推荐 Ubuntu 24.04 |
| 网络 | 能访问 Binance API | 香港节点天然满足 |
| Python | 3.11 - 3.13 | 推荐 3.13 |

### 网络验证

香港腾讯云可以直接访问 Binance，无需 VPN。部署后验证：

```bash
curl -s https://fapi.binance.com/fapi/v1/ping
# 应返回: {}
```

---

## 部署步骤

### Step 1: 系统初始化

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装基础工具
sudo apt install -y git curl wget build-essential

# 安装 Python 3.13（Ubuntu 24.04 可能需要 PPA）
sudo apt install -y python3.13 python3.13-venv python3-pip

# 如果 python3.13 不在 apt 中，用 deadsnakes PPA：
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.13 python3.13-venv python3.13-dev

# 安装 TA-Lib C 库（技术指标依赖）
sudo apt install -y libta-lib0-dev ta-lib
# 或者从源码编译：
# wget https://github.com/ta-lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4.tar.bz2
# tar -xjf ta-lib-0.6.4.tar.bz2
# cd ta-lib-0.6.4
# ./configure && make && sudo make install
# cd ..
```

### Step 2: 部署 freqtrade 代码

```bash
# 创建工作目录
mkdir -p /opt/freqtrade
cd /opt/freqtrade

# 克隆你的分支（替换为你的实际仓库地址）
git clone -b kelvin https://github.com/YOUR_USERNAME/freqtrade.git .

# 如果仓库是 private 的，先配置 SSH key 或使用 token：
# git clone -b kelvin git@github.com:YOUR_USERNAME/freqtrade.git .
```

### Step 3: 创建虚拟环境并安装依赖

```bash
cd /opt/freqtrade

# 创建虚拟环境
python3.13 -m venv .venv
source .venv/bin/activate

# 安装核心依赖
pip install --upgrade pip wheel setuptools
pip install -r requirements.txt

# 安装 FreqAI 依赖（LightGBM, scikit-learn, datasieve）
pip install -r requirements-freqai.txt

# 以开发模式安装 freqtrade
pip install -e .

# 验证安装
freqtrade --version
# 应输出: freqtrade 2026.3
```

### Step 4: 配置 Binance API Key

```bash
# 创建 .env 文件存储 API Key（不要提交到 git）
cat > /opt/freqtrade/.env << 'EOF'
BINANCE_KEY=你的API_KEY
BINANCE_SECRET=你的API_SECRET
COINGLASS_API_KEY=你的COINGLASS_API_KEY
EOF

chmod 600 /opt/freqtrade/.env
```

修改配置文件填入 API Key：

```bash
# 编辑 v5 配置
nano user_data/config_freqai_v5.json
```

将 `exchange.key` 和 `exchange.secret` 改为你的 Binance API Key：

```json
"exchange": {
    "name": "binance",
    "key": "你的API_KEY",
    "secret": "你的API_SECRET",
    ...
}
```

**重要：Binance API Key 权限设置**

- 开启：读取信息、合约交易
- 关闭：提币、内部划转
- IP 白名单：添加你的腾讯云服务器公网 IP

### Step 5: 上传必要文件

从本地机器上传以下文件到服务器：

```bash
# 在本地机器执行（Mac 终端）
SERVER=你的服务器IP

# 1. Coinglass 衍生品数据（必需，模型训练用）
scp -r user_data/data/coinglass/ root@${SERVER}:/opt/freqtrade/user_data/data/coinglass/

# 2. 已训练的 v5 模型（可选，节省首次启动时间）
#    如果不上传，首次运行时会重新训练（约 5-10 分钟）
scp -r user_data/models/freqai-15m-v5/ root@${SERVER}:/opt/freqtrade/user_data/models/freqai-15m-v5/

# 3. Binance 历史数据（必需，FreqAI 需要历史数据进行回测和训练）
#    这是最关键的数据，包含 BTC/ETH 的 15m/1h/4h K 线
scp -r user_data/data/binance/futures/ root@${SERVER}:/opt/freqtrade/user_data/data/binance/futures/

# 4. 策略文件和配置
scp user_data/strategies/FreqaiExampleStrategy_v5.py root@${SERVER}:/opt/freqtrade/user_data/strategies/
scp user_data/config_freqai_v5.json root@${SERVER}:/opt/freqtrade/user_data/
```

上传完成后确认目录结构：

```
/opt/freqtrade/
├── .env                                    # API Key
├── freqtrade/                              # 源码（git clone）
│   └── experimental/
│       └── coinglass_provider.py           # Coinglass 数据提供者
├── user_data/
│   ├── config_freqai_v5.json              # v5 配置
│   ├── strategies/
│   │   └── FreqaiExampleStrategy_v5.py    # v5 策略
│   ├── data/
│   │   ├── binance/
│   │   │   └── futures/                   # Binance K 线数据
│   │   │       ├── BTC_USDT_USDT-15m-futures.feather
│   │   │       ├── BTC_USDT_USDT-1h-futures.feather
│   │   │       ├── BTC_USDT_USDT-4h-futures.feather
│   │   │       ├── ETH_USDT_USDT-15m-futures.feather
│   │   │       ├── ETH_USDT_USDT-1h-futures.feather
│   │   │       └── ETH_USDT_USDT-4h-futures.feather
│   │   └── coinglass/                     # Coinglass 衍生品数据
│   │       ├── BTC_funding_rate_1h.feather
│   │       ├── BTC_open_interest_1h.feather
│   │       ├── ...（共 20 个文件）
│   └── models/
│       └── freqai-15m-v5/                 # 已训练的 LightGBM 模型
└── .venv/                                  # Python 虚拟环境
```

### Step 6: 修改配置为生产模式

编辑 `user_data/config_freqai_v5.json`：

```bash
nano user_data/config_freqai_v5.json
```

需要修改的关键配置：

```json
{
    "dry_run": false,              // 改为 false → 实盘交易
    "dry_run_wallet": 5000,        // 删除此行（仅 dry_run 用）
    "stake_amount": 200,           // 每笔 200 USDT
    "max_open_trades": 3,          // 最多 3 笔持仓

    "exchange": {
        "key": "你的API_KEY",
        "secret": "你的API_SECRET",
        // 删除 ccxt_config 中的 urls 覆盖（香港直连 Binance 不需要）
        "ccxt_config": {
            "options": {"defaultType": "swap"}
        }
    },

    "freqai": {
        "live_retrain_hours": 24,  // 实盘每 24 小时自动重训模型
        "save_backtest_models": false  // 实盘不需要保存回测模型
    }
}
```

**强烈建议先 dry_run 验证：**

保持 `"dry_run": true` 运行 1-2 天，确认策略正常下单、信号合理，再切换到实盘。

### Step 7: 用 systemd 注册为系统服务

```bash
sudo tee /etc/systemd/system/freqtrade-v5.service << 'EOF'
[Unit]
Description=Freqtrade FreqAI v5 Strategy
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/freqtrade
EnvironmentFile=/opt/freqtrade/.env
ExecStart=/opt/freqtrade/.venv/bin/freqtrade trade \
    --strategy FreqaiExampleStrategy_v5 \
    --config /opt/freqtrade/user_data/config_freqai_v5.json \
    --freqaimodel LightGBMRegressor \
    --logfile /opt/freqtrade/user_data/logs/freqtrade.log \
    --db-url sqlite:////opt/freqtrade/user_data/tradesv3_v5.sqlite
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

创建日志目录：

```bash
mkdir -p /opt/freqtrade/user_data/logs
```

启动服务：

```bash
# 重载 systemd
sudo systemctl daemon-reload

# 启动
sudo systemctl start freqtrade-v5

# 查看状态
sudo systemctl status freqtrade-v5

# 查看实时日志
journalctl -u freqtrade-v5 -f

# 设置开机自启
sudo systemctl enable freqtrade-v5
```

---

## 日常运维

### 常用命令

```bash
# 查看服务状态
sudo systemctl status freqtrade-v5

# 查看实时日志（最常用）
tail -f /opt/freqtrade/user_data/logs/freqtrade.log

# 重启服务（修改配置后）
sudo systemctl restart freqtrade-v5

# 停止服务
sudo systemctl stop freqtrade-v5

# 查看最近 100 行日志
journalctl -u freqtrade-v5 -n 100 --no-pager

# 查看进程资源占用
top -p $(pgrep -f freqtrade)
```

### Coinglass 数据定时更新

策略运行时需要最新的 Coinglass 数据。设置 cron 每小时更新：

```bash
# 编辑 crontab
crontap -e

# 添加以下行：每 4 小时更新一次 Coinglass 数据
0 */4 * * * cd /opt/freqtrade && .venv/bin/python scripts/download_coinglass_data.py \
    --symbols BTC ETH \
    --types funding_rate open_interest taker_volume liquidation long_short_ratio \
    --interval 1h \
    --limit 4500 \
    --output user_data/data/coinglass/ \
    >> user_data/logs/coinglass_update.log 2>&1
```

### Binance 历史数据定期更新

FreqAI 训练需要最新的历史数据：

```bash
# 每天凌晨 3 点更新 Binance 数据
0 3 * * * cd /opt/freqtrade && .venv/bin/freqtrade download-data \
    --pairs BTC/USDT:USDT ETH/USDT:USDT \
    --timeframes 15m 1h 4h \
    --exchange binance \
    --trading-mode futures \
    --datadir user_data/data/binance \
    >> user_data/logs/data_download.log 2>&1
```

### 监控关键指标

```bash
# 查看当前持仓
grep "Trade " /opt/freqtrade/user_data/logs/freqtrade.log | tail -5

# 查看模型训练情况
grep "Training model" /opt/freqtrade/user_data/logs/freqtrade.log | tail -5

# 查看入场/出场信号
grep -E "enter_long|enter_short|exit_long|exit_short" /opt/freqtrade/user_data/logs/freqtrade.log | tail -10

# 磁盘空间（模型文件会增长）
df -h /opt/freqtrade
du -sh /opt/freqtrade/user_data/models/
```

---

## 安全加固

### 防火墙

```bash
# 只开放 SSH（22）和 API（8080 仅本地）
sudo ufw allow 22/tcp
sudo ufw enable

# API 端口不对外开放（仅通过 SSH 隧道访问）
# 如果需要远程访问 API，用 SSH 隧道：
# 本地执行: ssh -L 8080:127.0.0.1:8080 root@你的服务器IP
```

### 日志轮转

```bash
sudo tee /etc/logrotate.d/freqtrade << 'EOF'
/opt/freqtrade/user_data/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF
```

### 模型清理

FreqAI 会不断产生新的训练模型，需要定期清理旧模型：

```json
// config_freqai_v5.json 中已设置：
"purge_old_models": 2   // 只保留最近 2 个模型版本
```

这会自动清理，但可以手动检查：

```bash
# 查看模型目录大小
du -sh /opt/freqtrade/user_data/models/freqai-15m-v5/

# 如果超过 1 GB，手动清理旧模型（保留最新的 2-3 个）
ls -lt /opt/freqtrade/user_data/models/freqai-15m-v5/ | head -5
```

---

## 首次部署验证清单

部署完成后，按以下步骤逐项验证：

```bash
# 1. 服务正常运行
sudo systemctl status freqtrade-v5
# 预期: Active: active (running)

# 2. Binance API 连通
grep "Using Exchange" /opt/freqtrade/user_data/logs/freqtrade.log | tail -1
# 预期: Using Exchange "Binance"

# 3. 策略加载成功
grep "Using resolved strategy" /opt/freqtrade/user_data/logs/freqtrade.log | tail -1
# 预期: Using resolved strategy FreqaiExampleStrategy_v5

# 4. FreqAI 模型加载/训练
grep "Training model on" /opt/freqtrade/user_data/logs/freqtrade.log | tail -1
# 预期: Training model on 390 features（390 = 360 传统特征 + ~30 Coinglass 特征）

# 5. Coinglass 数据可用
grep "CoinglassProvider" /opt/freqtrade/user_data/logs/freqtrade.log | tail -1
# 预期: 无 "not available" 警告

# 6. 干跑模式下观察信号（保持 dry_run: true 至少 24 小时）
grep -E "enter_long|enter_short" /opt/freqtrade/user_data/logs/freqtrade.log | tail -5
# 预期: 能看到入场信号产生

# 7. 确认无误后切换到实盘
# 修改 config: "dry_run": false
# sudo systemctl restart freqtrade-v5
```

---

## 故障排查

### 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 服务启动后立即退出 | 配置文件语法错误 | `freqtrade test-config --config user_data/config_freqai_v5.json` |
| 无法连接 Binance | 网络问题 | `curl https://fapi.binance.com/fapi/v1/ping` 检查 |
| CoinglassProvider not available | 缺少 experimental 模块 | 确认 `freqtrade/experimental/coinglass_provider.py` 存在 |
| 模型训练失败 NaN 过多 | 数据不完整 | 重新下载 Binance 历史数据 |
| 交易被拒绝 | API Key 权限不足 | 检查 Binance API Key 权限 |
| 内存不足 OOM | LightGBM 训练内存 | 增加 swap 或升级内存 |

### 增加 Swap（4GB 内存不够时）

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 紧急停止

```bash
# 立即停止策略
sudo systemctl stop freqtrade-v5

# 如果有未平仓位，通过 Binance 网页/App 手动平仓
# 或使用 freqtrade CLI 强制平仓：
cd /opt/freqtrade && source .venv/bin/activate
freqtrade forcesell --config user_data/config_freqai_v5.json --all
```

---

## 从本地一键部署脚本

在本地 Mac 上创建一键部署脚本：

```bash
#!/bin/bash
# deploy_v5.sh — 一键部署 v5 到腾讯云
set -e

SERVER=${1:?"用法: ./deploy_v5.sh <服务器IP>"}
REMOTE=/opt/freqtrade

echo "=== 部署 FreqAI v5 到 ${SERVER} ==="

# 上传数据文件
echo "[1/4] 上传 Coinglass 数据..."
scp -r user_data/data/coinglass/ root@${SERVER}:${REMOTE}/user_data/data/coinglass/

echo "[2/4] 上传 Binance 历史数据..."
scp -r user_data/data/binance/futures/ root@${SERVER}:${REMOTE}/user_data/data/binance/futures/

echo "[3/4] 上传策略和配置..."
scp user_data/strategies/FreqaiExampleStrategy_v5.py root@${SERVER}:${REMOTE}/user_data/strategies/
scp user_data/config_freqai_v5.json root@${SERVER}:${REMOTE}/user_data/

echo "[4/4] 上传已训练模型（可选，加速首次启动）..."
scp -r user_data/models/freqai-15m-v5/ root@${SERVER}:${REMOTE}/user_data/models/freqai-15m-v5/

echo "=== 部署完成 ==="
echo "下一步: SSH 到服务器，编辑 config 填入 API Key，然后启动服务"
echo "  ssh root@${SERVER}"
echo "  nano ${REMOTE}/user_data/config_freqai_v5.json"
echo "  systemctl start freqtrade-v5"
```
