# sh-store-insight PRD 一致性 Lint 报告

> 生成时间：2026-07-27（v3.5 复核）
> 校验对象：`sh-store-insight_PRD.md`（v3.5，工作区唯一真源；Codex 侧同名 v3.5.md）
> 校验脚本：`lint_prd.py`（基于真实 PRD 文本 + `run_tests.py` 实跑解析）

## 基础事实
- FR 定义总数：**40**（FR-1..FR-40，编号连续无缺漏/重复）
- ✅ 计数 = **18**（与 PRD 封面「已实现 18 项 FR」一致）
- 🔲 计数 = **22**（与 PRD 封面「规划 22 项 FR」一致）
- 真实测试 ID 数（`run_tests.py` 中 `R.ok/R.fail`）：**38**（与「38/38 通过」一致；T37=FR-39 M1 PoC，T38=FR-37 M1 PoC）

## v3.3 变更（对照评审 5 项优化建议）
吸收"元数据联邦治理 / 细粒度溯源 / W3C PROV 纠偏 / PBDS 数据跳过 / RO-Crate 复现"评审，新增 **FR-34~FR-40（全部 🔲）**，并同步更新 §1.3 论文表、§4 术语、§2.3 边界、§7.1 数据流、§11 路线图、§12 指标、§13.6 联邦目录、§13.7 溯源草图、§13.9 断言与复现包、§14.6 W3C PROV 信任链。所有引用经 Web 实跑核实（Smoke arXiv:1801.07237 / GProM TaPP2014 / I-Rex VLDB2020 / PBDS PVLDB14-15 arXiv:2104.12815 / RO-Crate PLOS ONE 2024 / W3C PROV-DM / Semirings PODS2007）。

### v3.3 二次细化：FR-34~40 验收标准（DoD）+ 里程碑
- 在 §13.6.1 / §13.7.1 / §13.9.1 / §13.9.2 / §14.6 各小节末尾，为 FR-34~40 补充了**可测试验收标准（AC1~AC4）**与**分阶段里程碑（M1~M3，含 P1/P2/P3 与基于真实 629k×152 parquet 的演示/基准要求）**；所有条目均用未来时态，未改变 🔲 规划状态。
- 同步修正 §13.9.2 的一处引用笔误（"见 §14.7" → "见 §14.6"）。
- Codex 侧工作文件名由 `sh-store-insight_PRD_v3.2.md` 重命名为 `sh-store-insight_PRD_v3.3.md`，与正文版本号对齐；工作区唯一真源 `sh-store-insight_PRD.md` 内容不变。
- 重跑 `lint_prd.py`：FR 计数仍 18✅ / 22🔲，五道校验全绿，🔲 FR 无一被误写成已交付。

## 校验结论

### 1. §11/§13/§14 引用的 FR 是否都在 §5 定义 → ✅ 通过
全部被引用的 FR（含 §11 路线图的 FR-26~30、FR-22/23/31/33，§13 的 FR-22/23/31/33，§14 的 FR-27/28）均在 §5 定义，**无悬空引用**。

### 2. 每个 ✅ FR 是否都有对应真实测试 → ✅ 通过
全部 18 个 ✅ FR 均映射到 `run_tests.py` 中真实存在的测试（T01–T36）：

| FR | 覆盖测试 | FR | 覆盖测试 |
|---|---|---|---|
| FR-1 | T01–T08 | FR-10 | T15/T25/T36 |
| FR-2 | T09–T12 | FR-11 | T16 |
| FR-3 | T13–T14 | FR-12 | T22–T24 |
| FR-4 | T01/T02/T04/T08 | FR-13 | T25/T30 |
| FR-5 | T03/T05/T07/T28 | FR-14 | T26–T32 |
| FR-6 | T07 | FR-15 | T34 |
| FR-7 | T20 | FR-16 | T33 |
| FR-8 | T13/T14（间接） | FR-17 | T35 |
| FR-9 | T21 | FR-18 | T36 |

> 说明：FR-8（维度自适应）由 C 类体检测试间接覆盖，未单独断言「自动跳过不适用维度」逻辑；如需更硬的证据可补一条负向断言（食杂不报点评维度）。

### 3. 每个 🔲 FR 是否被正文写成已交付 → ✅ 通过
对全部 22 个 🔲 FR（含新增 FR-34~40）做了「交付关键词（已实现/已落地/已支持…）」扫描，并结合 🔲/规划/目标/尚未 等反向词排除，**无一处把规划能力误写成已交付**。

### 4. 附加：§2.3 能力边界 ↔ §5 状态一致性 → ✅ 通过
§2.3 标注的 🔲 范围（HIP FR-26~30、数仓 FR-22/23/31/33、BI FR-32）与 §5 FR 状态表完全一致。

### 5. 附加：DuckDB 无独立 FR → ✅ 已修复
文中 DuckDB 出现 **5 次**，已在 **FR-23** 描述中显式点名「DuckDB 作为本地列式 Adapter 候选，与 pandas/DataFusion 并列」，FR 表中不再缺失，读者不会误判为已支持。

## v3.4 变更说明（FR-39 M1 PoC 落地）

- 新增 `scripts/data_assertions.py`（FR-39 数据质量断言 M1 PoC）：`build_baseline` / `generate_dcs` / `check_dcs` / `scan_drift`，零 LLM、确定性、仅依赖 pandas。
- `run_tests.py` 新增 **T37**（数据断言-DC漂移检测）：在 629,074×152 真实 parquet 抽样（渠道组/省份 5,000 行）建基线；合成注入均值突变 / 空值率飙升 / 值域漂移三类漂移均被捕获，干净快照 0 告警，基线 version 随数据改动自动变化。实跑 **37/37 通过，exit=0**。
- 测试计数 36→37：PRD §2.3/§9/§12(M1) 与封面同步更新；FR-39 行与 §13.9.1 加「验证状态」注记（明确 M1 PoC 已落地、功能整体仍 🔲 规划，未翻 ✅）。
- `lint_prd.py` 补 `FR-39 → T37` 映射；重跑**五道校验全绿**（18✅/22🔲 不变，🔲 FR 无一被误判为已交付）。

## v3.5 变更说明（feedback_log.py 从雏形→独立可测 + FR-37 M1 PoC）

- 把 §7.2 规划模块 `feedback_log.py` 从 `memory.py` 的 `correct/corrections` 雏形，升级为**独立、结构化、可取证**的 HIP 纠偏审计模块：`scripts/feedback_log.py` 落地 `log_correction/confirmation/jargon`、`review`（approved/rejected 状态机）、`to_prov`（输出 W3C PROV-DM 的 Entity/Activity/Agent 三元组 PROV-JSON bundle）、`pending` 待审队列；强制高风险/模糊匹配进入 pending 并携带 `match_method/match_score/review_status/risk_level` 元数据（FR-37 AC1/AC3）。零 LLM、确定性，持久化到独立 `feedback_log.jsonl`（与 RAG 记忆解耦）。
- `run_tests.py` 新增 **T38**（HIP 纠偏审计全链路）：高风险模糊纠偏「晚10点后火锅店」→ pending+元数据 → PROV 三元组可序列化 → 审核通过/驳回 → 持久化重载。实跑 **38/38 通过，exit=0**（总耗时 ~53s）。
- 测试计数 37→38：PRD §2.3/§9/§12(M1)/封面同步更新；FR-37 行与 §14.6 加「M1 PoC 已验证(T38)」诚实注记，**功能整体仍 🔲 规划、未翻 ✅**；§7.2 模块表 `feedback_log.py` 行标注「M1 PoC 已落地，整体仍 🔲」。
- **诚实边界**：M2（provtoolbox 真实验证）本环境未装 provtoolbox/provpy，T38 仅做结构一致性校验；§14.6 已明确标 ⚠ 待做。
- `lint_prd.py` 补 `FR-37 → T38` 映射；重跑**三道核心 + 两道附加校验全绿**（18✅/22🔲 不变，🔲 FR 无一被误判为已交付，DuckDB 已在 FR-23 收口）。
- Codex 侧 `sh-store-insight_PRD_v3.4.md` 覆盖为 **v3.5.md** 并删除旧 v3.4（文件名↔内容版本对齐）。

## 结论
**三道核心校验 + 两道附加校验全部通过，文档前后一致、无前后矛盾。** DuckDB 的 FR 表述已纳入 FR-23，无遗留提示项。FR-37 的 M1 PoC（T38）与 FR-39 的 M1 PoC（T37）均已实跑验证，二者功能整体仍属 🔲 规划。
