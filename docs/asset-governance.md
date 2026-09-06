# Asset Governance — 仓库资产分类与治理（issues #5.1 / #5.2）

> 批次 3 整改记录 · 2026-09-06 · 执行: AssetGov5
> 约束: 本次仅允许的 git 索引操作为对下述两个文件的 `git rm --cached`；未触碰 `data/` 与 `output/`。

## 1. 结论速览

| 资产 | 分类 | 处置 |
|---|---|---|
| `skill/references/query_memory.jsonl` | **运行时缓存**（含私有数据执行产物，运行时变异） | `git rm --cached`，本地保留，`.gitignore` 屏蔽 |
| `skill/references/query_memory_emb.npz` | **派生二进制**（由 jsonl 经本地 embedding 生成） | 同上；缺失时运行时自动重建 |

## 2. Issue #5.1 — query_memory.jsonl / query_memory_emb.npz

### 2.1 判定依据：运行时缓存（非受管资产）

**写入方（仓库内真实代码，均为运行时路径）：**
- `skill/scripts/memory.py:23-24` — `MEM_PATH` / `EMB_PATH` 常量；`QueryMemory.record`（:137-151，成功查询）与 `QueryMemory.correct`（:153-169，用户纠偏）经 `_save`（:125-134）**整文件重写** jsonl 并 `np.savez` 写 npz。
- `skill/scripts/feedback_integration.py:22,32-44` — `FailureFeedbackRecorder.record_rejection` 追加 `failure_feedback` 记录；由 `dual_router.py:69-73` 在拒答时自动调用；`skill/SKILL.md:106` 描述该失败案例回流闭环。
- 滚动上限 `MAX_RECORDS = 300`（`memory.py:25,148`）——**设计上每次查询都会变异**，任何入库副本在下一次查询后即过时。
- `skill/scripts/run_tests.py:952-988` 的记忆护栏将这两个文件视为**需备份/恢复的用户运行态**（测试前备份为 `.bak`、清空、测试后还原），进一步佐证其"用户本地状态"属性。

**内容含私有数据执行产物（抽样核查，2026-09-06，26 条记录）：**
- 1 条 `success` 记录携带 `rows=346, cols=153` —— 这是针对未入库私有数据集（`data/`）执行后的**结果形状元数据**，无法由已提交输入推导。
- 25 条为旧格式（缺 `kind` 字段）的近重复记录（同一问题重复 4-6 次），来自针对私有数据集的反复开发/测试运行 —— 运行时污染，非策划内容。
- 词汇说明：问题文本中的词元（如「美宜佳」「是否匹配」）**同样出现于已提交参考文件**（`golden_eval_dataset.json`、`field_registry.json`、`docs/fmcg_52_query_benchmark.md:51`），即词汇本身是公开的；不可再生的部分是执行产物元数据与运行时累积记录。

**npz 的性质：** 纯派生二进制（对 jsonl 中 `nl` 字段做 fastembed `BAAI/bge-small-zh-v1.5` 向量化），不可在 git diff 中人工审阅；`memory.py:102-114` 在 npz 缺失/与记录数不对齐时**自动全量重算**（300 条内 <1s），入库零收益。

### 2.2 已执行动作（2026-09-06）

1. 恢复本地副本：当日 16:10:12 一次中断的 `run_tests.py` 护栏周期（`_backup_memory` 已执行、进程在 `_restore_memory` 前崩溃）遗留了 `.bak` 与 `skill/.mem_guard_state.json`；经 SuiteGuard 确认为废弃状态后，按护栏语义完成还原（`.bak` → 原名，删除 `.bak` 与状态文件）。
2. `git rm --cached skill/references/query_memory.jsonl skill/references/query_memory_emb.npz` —— 索引移除、本地文件保留。
3. `.gitignore` 追加（第 95-103 行，append-only）：`skill/references/query_memory.jsonl`、`skill/references/query_memory_emb.npz` 及对应 `.bak` 模式。
   - **勘误**：HEAD 中 `.gitignore` 第 62-64 行已有的 `references/query_memory.*` 模式因含路径分隔符而锚定在仓库根，匹配不到 `skill/references/` 下的实际文件（修复前 `git check-ignore -v` 退出码 1 已验证）；本次按 `skill/` 前缀重新声明，旧行按 append-only 约束保留不动。

### 2.3 再生路径（如实说明）

- **jsonl：不可由已提交输入再生。** 它是运行时自学飞轮，随使用重新积累（成功查询/纠偏/拒答回流）。全新开始 = 删除该文件（`memory.py:_load` 对缺失文件优雅返回空列表，Skill 自动降级运行）。
- **npz：可由本地 jsonl 确定性再生**，两条路径：
  1. 自动：`QueryMemory` 加载时检测 npz 缺失/与记录数不对齐，即在内存中全量重算（`memory.py:102-114`）；npz 文件随后续任意 `record/correct` 写入经 `_save` 落盘（`memory.py:125-134`），纯检索场景不回写；
  2. 显式：`python skill/scripts/regenerate_query_memory.py`（`--check` 只校验，`--mem` 指定路径）——该脚本是对 memory.py 同一写入路径的薄封装，**不生成、不虚构任何记忆内容**。
  - 前提：fastembed 可用（模型 `BAAI/bge-small-zh-v1.5`）；不可用时运行时自动降级 bigram Jaccard，无需 npz。

### 2.4 关于 npz 二进制的可审性

若两文件保留跟踪，npz 为二进制向量缓存，无法在 code review 中人工审阅 —— 本应为保留跟踪的理由做显式论证；因判定为运行时缓存已移出跟踪，此条仅存档备查（issue #5 问询）。

## 3. Issue #5.2 — 配额语义回流（quota semantics reflux）证据包

> **状态：上游证据包草稿。** 供仓库维护者整理后提交上游 prism-ontology 的 `Quota` 提案
> （对应 upstream issue #6 的 6 步治理流程）。本文档本身**不是**上游提案，仅汇编本仓库内
> 配额语义的落点证据（file:line）。**Owner = 仓库维护者。**

| # | 语义环节 | 位置 | 要点 |
|---|---|---|---|
| 1 | NL → 配额意图解析 | `skill/scripts/nl_parser.py:170-176` | 正则抽取「选/圈选/前 N/TOP N/N 家/户」→ `q["limit"]` + `q["target_quota"]`；DSL 契约含 `target_quota` 字段（`nl_parser.py:64`，默认 `None` 见 :71，explain 导出见 :334） |
| 2 | DSL 执行与配额达成评估 | `skill/scripts/agent_core.py:655-682` | 构造 `quota_evaluation`：`target_quota`（回退取 `limit`，:656）、`pool_size`、`selected_size`、`fulfillment_rate`、`fulfillment_status`（`fulfilled`/`gap_detected`）、`gap_count`、`cutoff_thresholds`（截断行阈值，覆盖字段：周末均值客流(人)、大众点评餐饮点评评分星级、月均收入潜力预测(无协议无冰柜)、携程-房间数、营业面积） |
| 3 | 洞察层消费 | `skill/scripts/insight.py:498,514-520,544-545,552,590-592` | 读取 `quota_evaluation` → 注入 `structure` → `gap_count>0` 触发缺口警示 → CLI 渲染「目标/实际/状态/缺口」 |
| 4 | 契约测试 | `skill/scripts/test_priority_quota.py:34-49,61-81,84-98` | 三个业态锚点：冷柜 200、餐饮 100（含 cutoff 阈值断言）、酒店 KA 50；同时断言 DSL `target_quota` 与 `quota_evaluation` 各字段 |
| 5 | 基准文档 | `docs/fmcg_52_query_benchmark.md:51` | Q02 以 TOP-N 表述目标子区域；注：全文无字面 `quota/配额` 命中，配额语义在本仓库由「优先级排序 + limit/target_quota」机制承载 |

**上游提案映射注记（供维护者参考，非结论）：** 本仓库的配额语义 = 「意图级目标容量（target_quota）」与「普通 limit」的双轨：target_quota 存在时额外产出 pool/selection/fulfillment/gap/cutoff 五元组证据，且已有契约测试锚定。若上游 prism-ontology 引入 `Quota` 概念，建议至少覆盖：配额目标、候选池、圈选结果、达成状态（fulfilled/gap_detected）、缺口数、截断阈值（cutoff_thresholds）六要素 —— 本仓库实现可作为该提案的参考实现与测试用例来源。

## 4. 变更清单（本批次）

- `skill/references/query_memory.jsonl` / `query_memory_emb.npz`：索引移除（`git rm --cached`），本地保留并已入 `.gitignore`。
- `.gitignore`：末尾追加运行时缓存忽略块（第 95-103 行，append-only，含 62-64 行锚定失效的勘误说明）。
- `skill/scripts/regenerate_query_memory.py`：新增，npz 再生/校验工具（封装 memory.py 既有写入路径，无新增语义）。
- `docs/asset-governance.md`：本文档。
