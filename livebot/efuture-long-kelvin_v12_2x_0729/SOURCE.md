# SOURCE — efuture-long-kelvin_v12_2x_0729

- 创建日期：2026-07-29
- git commit：`76ea8187b`（kelvin 分支）
- 用途：当前最佳策略快照，供下一步 dry-run/实盘使用

## 文件来源

| 文件 | 来源 |
|---|---|
| `EfutureLongKelvin_v12_2x_lock.py` | `user_data/strategies/efuture-long-kelvin/EfutureLongKelvin_v12_2x_lock.py`（原样复制） |
| `EfutureLongKelvin_v12_2x_lock.json` | `user_data/strategies/efuture-long-kelvin/EfutureLongKelvin_v12_2x_lock.json`（原样复制，与 v12_2x 参数逐字节一致） |
| `config.json` | `user_data/configs/backtest/EfutureLongKelvin.json` 复制 + 两处修改：移除 `TON/USDT:USDT`（已被币安合约下架，数据止 2026-06-11）；`bot_name` 改为 `EfutureLongKelvin_v12_2x`。dry_run 保持 `true` |

## 验证依据

- 2026-06-25 优化报告：滚动 10 窗口 CV=0.470，10/10 盈利，最大 DD 30.0%（`user_data/strategies/efuture-long-kelvin/EfutureLongKelvin_optimization_report.md`）
- 2026-07-29 新数据复验：数据补齐至 2026-07-29 后 3 个新窗口全部盈利（+45.60% / +16.73% / +9.12%，最大 DD 10.41%，6 月市场 -23.63% 仍 +9.12%），详见 `user_data/strategies/efuture-long-kelvin/LINEAGE.md`

## 启动命令

```bash
freqtrade trade \
  -c livebot/efuture-long-kelvin_v12_2x_0729/config.json \
  -c user_data/configs/secrets/binance.main.json \
  -c user_data/configs/secrets/telegram.efuture_kelvin.json \
  -c user_data/configs/secrets/apiserver.efuture_kelvin.json \
  --strategy-path livebot/efuture-long-kelvin_v12_2x_0729 \
  --strategy EfutureLongKelvin_v12_2x_lock
```

注意：策略在 `user_data/strategies/` 之外，必须带 `--strategy-path`；参数文件与策略同目录同基名，自动加载。密钥用 `user_data/configs/secrets/` 命名片段按名叠加（测试换 `binance.test`/`telegram.test`，见该目录 README）。上实盘前把 config.json 的 `dry_run` 改为 `false`，并确认 `binance.main.json` 已填入轮换后的新 key（T14 密钥轮换是前置）。
