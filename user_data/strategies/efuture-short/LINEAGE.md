# efuture-short 谱系

以下仅记录目录内文件可证实的事实。

| 版本 | 来源 | OOS 结果 |
|---|---|---|
| EfutureShort | 本线基线（`EfutureShort.py/.json`） | 未记录 |
| EfutureShort_Iter14 | autoresearch 实验迭代第 14 轮（实验号命名，未按 `_v<N>` 规范升代） | 未记录 |
| best_Iter24_score1.4007 | autoresearch 迭代第 24 轮最佳参数（`EfutureShort_best_Iter24_score1.4007.json`，附 `EfutureShort_best_combined.json` 与 `EfutureShort_best_reference.py`） | score 1.4007（autoresearch 内部评分） |

## 注意事项

- **已知重复类问题**：`EfutureShort.py` 与 `EfutureShort_base.py` 存在重复的类定义（同 strategy 类名多份拷贝），加载时可能相互覆盖。尚未裁定保留哪一份，清理前勿删任一文件。
- 命名遗留问题：`EfutureShort_Iter14.py`（实验号命名）与 `EfutureShort_best_Iter24_score1.4007.json`（状态后缀命名）均不符合现行命名规范，保留原样以维持可追溯性。
