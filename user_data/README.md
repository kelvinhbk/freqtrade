# user_data 组织规范

> 本文档是 user_data 的组织规范，设计依据见 docs/superpowers/specs/2026-07-25-userdata-reorganization-design.md

## 目标目录结构

```
user_data/
├── strategies/
│   ├── efuture-iter/            # 实盘线（EfutureIter + 谱系起点 E0033 + 审查报告）
│   ├── efuture-long-kelvin/     # EfutureLongKelvin 循环优化线（v1/v2/v12_2x + 优化报告）
│   ├── efuture-long/            # EfutureLong 非 Kelvin 系列（v1/v2/v4），单独成线
│   ├── efuture-short/           # EfutureShort 线
│   ├── enew/                    # ENEW 线（预留，实验产物在 experiments/autoresearch_enew/）
│   ├── freqai-v6/               # FreqAI v6 线（FreqaiExampleStrategy_v6）
│   └── experiments/             # autoresearch 各实验策略目录（保留 autoresearch 原生命名）
├── configs/
│   ├── live/                    # dry_run=false；每策略线最多一个主 config
│   ├── dryrun/                  # dry_run=true
│   ├── backtest/                # 回测/循环优化用 config
│   ├── secrets/                # 命名密钥片段目录（gitignored，按 binance./telegram./apiserver.<名称> 区分，启动用多个 -c 按名叠加，仅其 README.md 入库）
│   └── README.md                # config 清单：用途、策略线、最后验证日期
├── autoresearch/                # 框架代码 + evolution_config_*.json 保留原位
├── archive/
│   ├── e0v1e/                   # 原 originalStratergy/（已修正拼写）
│   ├── backup/
│   ├── before-live-bot/
│   └── params-archive/          # 删除大产物前导出的 top-5 参数存档
├── livebotBackup/               # 旧部署快照备份（2026-08-02 由 livebot/ 改名，不再新增；现役快照在仓库根 livebot/）
└── README.md                    # 本文档
```

每条策略线目录下放 `LINEAGE.md`，三列表格：版本 | 来源（父版本/实验号）| OOS 结果一句话。

## 命名规范

| 对象 | 规则 | 示例 | 反例 |
|---|---|---|---|
| 策略目录 | 小写连字符，策略线名 | `efuture-long-kelvin/` | `Efuturelong/`、`freaiStr/` |
| 策略文件 | 类名同名，代际 `_v<N>` | `EfutureLongKelvin_v12.py` | `EfutureShort_Iter14.py` |
| 参数文件 | 与策略同名 | `EfutureLongKelvin_v12.json` | `EfutureLong_v4_hyperopt.json` |
| config | `<策略线>.json`，变体点分后缀 | `EfutureIter.opt.json` | `config_EfutureIter_live_opt_noproxy.json` |
| 实验产物 | autoresearch 原生命名不动，晋级时改名 | `ENEW_E0036.py` | — |
| 部署快照 | `livebot/<策略线>_v<N>_<MMDD>/` | `livebot/efuture-iter_v3_0725/` | `livebot/20250202version/` |
| 归档 | `archive/<原用途>/` | `archive/backup/` | `originalStratergy/` |

补充规则：

- 代际命名：`XxxYyy_v<N>.py` 只在策略逻辑结构性变化时升 N；纯参数调优不升版本
- git 即历史：不保留 `v11_backup.py` 类文件；`_loop_state.json`、optimization_report 随代码提交
- autoresearch 晋级规则：实验产物被采纳时才复制进策略线目录并改名升代；原实验目录保留在 experiments/ 或 autoresearch/results/ 下
- config 规则：环境 × 策略线 = 最多一个"当前" config；proxy 等环境差异移到 shell 环境变量；config 内路径保持相对；`live/` 修改单独 commit
- 部署快照规则：新实盘部署 = 新建 `livebot/<策略线>_v<N>_<MMDD>/`，从 configs/live/ + strategies/<线>/ 复制；复制后放 `SOURCE.md` 记录 git commit hash；旧快照不再改动，退役后整目录移入 archive/
- 最佳策略快照规则："当前效果最好的策略"要复制一份供下一步使用时，同样走部署快照规则——新建 `livebot/<策略线>_v<N>_<MMDD>/`，含策略 .py、参数 .json、config.json、SOURCE.md 四件套；config 从源 config 复制并按实况修正（如移除已下架交易对），dry_run 保持 true；SOURCE.md 必须记录来源路径、git commit hash、验证依据与启动命令（策略在 strategies/ 之外，启动须带 `--strategy-path`）。当前快照：`livebot/efuture-long-kelvin_v12_2x_0729/`（2026-07-29，依据见 efuture-long-kelvin/LINEAGE.md）

## 两条核心原则

1. 文件名只表达"是什么"，不表达状态/用途/环境——状态由目录表达，代际由 `_v<N>` 表达
2. 一个名字一处含义：类名 `EfutureIter` ↔ 线名 `efuture-iter` 机械对应

## 产物三层保留策略

| 层 | 内容 | 策略 |
|---|---|---|
| 结论层（进 git） | LINEAGE.md、被采纳版本 .py/.json、optimization_report、每轮最佳参数摘要 | 永久 |
| 证据层（留盘不进 git） | 活跃实验 results/、最近 1-2 轮 hyperopt 结果 | 实验活跃期间保留 |
| 过程层（定期删） | 已结束实验 results、历史 .fthypt、backtest 中间产物 | 导出摘要后删除 |

总原则：git 存"结论"，磁盘只留"还会再用的证据"；回测/hyperopt 产物是数据和代码的函数，可重算，不囤积。
