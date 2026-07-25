# configs 清单

| 文件 | 环境 | 策略线 | 说明 | 最后验证 |
|---|---|---|---|---|
| live/EfutureIter.json | 实盘 dry_run=false | efuture-iter | 真身（2026-07-25 裁定） | 2026-07-25 |
| dryrun/EfutureIter.json | 模拟盘 | efuture-iter | 原 config_EfutureIter_kelvin.json | 2026-07-25 |
| backtest/EfutureLongKelvin.json | 回测/循环 | efuture-long-kelvin | 原 config_opt_loop.json | 2026-07-25 |
| backtest/FreqaiV6.json | 回测 | freqai-v6 | 原 freaiStr/config_freqai_v6.json | 2026-07-25 |

规则：环境×策略线最多一个主 config；密钥一律在 secrets.local.json（gitignored），启动用多个 -c 合并；live/ 修改单独 commit 并写明原因。
