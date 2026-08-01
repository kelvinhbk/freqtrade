# 命名密钥片段（secrets/）

多个 Telegram bot token 与币安 API key 按名称区分，用于测试 / 多 bot 交易。本目录**全部 gitignored（仅本 README 入库）**，启动时按名用多个 `-c` 叠加合并（freqtrade 规则：后者覆盖前者）。

## 命名规范

| 前缀 | 含义 | 覆盖的 config 段 |
|---|---|---|
| `binance.<名称>.json` | 币安 API key/secret | `exchange.{key,secret}` |
| `telegram.<名称>.json` | Telegram bot token + chat_id | `telegram.{enabled,token,chat_id}` |
| `apiserver.<名称>.json` | 某 bot 的 WebUI/REST 凭证 | `api_server.{username,password,jwt_secret_key,ws_token}` |

- `<名称>` 表达用途或所属 bot：`main`=实盘主账户、`test`=测试、`<bot>`=某策略线专属（如 `efuture_kelvin`）
- 测试 vs 实盘 = 启动时选 `binance.test` 还是 `binance.main`
- 一个凭证只存一处；新增凭证 = 复制一个片段改名填值

## 当前片段

| 文件 | 用途 | 状态 |
|---|---|---|
| `binance.main.json` | 币安主账户交易 key | 待填（轮换后新 key） |
| `binance.test.json` | 币安测试 key | 待填 |
| `telegram.efuture_kelvin.json` | efuture-long-kelvin 主 bot 通知（chat_id 已保留） | 待填 token |
| `telegram.test.json` | 测试 bot token | 待填 |
| `apiserver.efuture_kelvin.json` | efuture-long-kelvin 的 WebUI 凭证 | 已填（T14 生成的随机值） |

> 旧的单一 `secrets.local.json` 已于 2026-07-31 完全迁移到本目录并删除。旧币安 key 备份在 `~/.freqtrade_old_binance_key_20260729.json`（本地，权限 600）。

## 启动示例

```bash
# efuture-long-kelvin 实盘主账户 + 主 bot 通知
freqtrade trade \
  -c livebot/efuture-long-kelvin_v12_2x_0729/config.json \
  -c user_data/configs/secrets/binance.main.json \
  -c user_data/configs/secrets/telegram.efuture_kelvin.json \
  -c user_data/configs/secrets/apiserver.efuture_kelvin.json \
  --strategy-path livebot/efuture-long-kelvin_v12_2x_0729 \
  --strategy EfutureLongKelvin_v12_2x_lock

# 同一 bot 用测试账户/测试 bot → 只换 binance/telegram 片段名
  -c user_data/configs/secrets/binance.test.json \
  -c user_data/configs/secrets/telegram.test.json \
```

注意：telegram 片段内置 `enabled=true`，选中即开启通知；不叠加 telegram 片段则沿用主 config 的 `telegram.enabled`。
