# 谋圣·售点洞察（Mousheng Outlet Insight）产品需求文档（PRD）

> **文档版本**：v1.3.0-rc3（独立 Insight 组件 RC3 核心基线）  
> **所属品牌**：棱镜极 TopPrism ｜ 所属产品：谋圣 Mousheng ｜ 技术标识：`mousheng-outlet-insight`  
> **核心定位**：世界模型之上的独立认识组件（Insight Component）。只负责看懂售点数据并提炼清晰、可信、可追溯的结构化洞察。  
> **核心承诺**：「一句话提出售点问题，获得一份清晰、可信、可追溯的洞察。」  
> **生效日期**：2026-08-26  

---

## 1. 产品定位与三环节解耦架构

### 1.1 正式定义
谋圣·售点洞察是谋圣产品体系中的独立售点认识与洞察组件（Insight Component）。它接收已经确定范围和口径的数据证据集合与质量上下文，识别其中的关键事实、结构特征、群体差异、统计异常、配额达成与适用边界限制，输出带数据证据与置信度评估的标准 `InsightPackage`，并支持生成多维 Executive 深度研报。

它只负责回答“数据反映了什么、结构与差异是什么、哪些对象存在异动、结论有多可靠、还应继续分析什么”，**坚决不替用户进行派单、排程、路线、CRM 状态变更或渠道业务决策**。

### 1.2 三环节解耦架构

为避免产品边界无序膨胀，系统在架构上严格划分三个环节：

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ 【1. Analysis Gateway（外围输入适配器 / 演示网关）】                           │
│  - 职责：自然语言解析、意图提取、DSL 编译、Dry-plan 预览、会话工作集管理         │
│  - 定位：属于外部输入适配器，不属于 Insight 核心产品定义                      │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ 提交 AnalysisIntent
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 【2. Analysis Engine（双轨执行引擎 / 数据层）】                               │
│  - 主轨：Vanna 2.0 (MIT) + 确定性 SQL 静态防火墙 (firewall.py) + DuckDB (Parquet)│
│  - 规则轨：DSL + RuleQuery (处理多规则多因子综合达标率打分排行)               │
│  - 职责：毫秒级数据过滤、聚合计算、数据质量体检 audit                         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ 交付 EvidenceSet (v1.2) + QualityContext
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 【3. Insight Component（谋圣·售点洞察 —— 核心产品本体）】                     │
│  - 职责：确定性统计洞察综合、集中度评估、离群异常识别、质量限制说明、追问推荐│
│  - 增强：Evidence Semantics v1.2 显式度量单位/粒度、目标配额与截断分析、        │
│          scipy 显著性检验 (Mann-Whitney U) 与 Placebo 因果反驳检验            │
│  - 定位：世界模型之上的独立认识器官（第二棱镜），输出标准 11 顶级字段包       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 标准三元输入与输出契约

> **权威示例**：所有标准契约示例见 [`examples/`](../examples/) 目录：
> - [`examples/insight-request.example.json`](../examples/insight-request.example.json) —— InsightRequest 标准输入契约
> - [`examples/insight-package.example.json`](../examples/insight-package.example.json) —— InsightPackage 标准输出契约

### 2.1 标准三元输入契约 (`InsightRequest v1.2`)
Insight 组件不直接拥有底层数据库，只消费规范化的三元输入：

1. **`AnalysisIntent`（分析意图）**：业务提问、意图类型（清单/分布/规则评估/集中度/异常）、目标实体、空间与业态范围、指标与规则、**`target_quota`（目标容量配额）**、**`priority_funnel`（优先级漏斗）**。
2. **`EvidenceSet`（证据集合 v1.2）**：
   - `dimensions`：显式声明字段名称、本体概念 URI、类型与基数。
   - `measures`：显式声明度量字段、本体度量 URI、**度量物理单位 (如 家, 元/月, 星)**、聚合类型与数据类型。
   - `grain`：物理数据粒度 (`brand`, `outlet`, `commercial_site`, `territory` 等)。
   - `time_window`：时间快照与时间粒度范围。
   - `quota_evaluation`：目标配额、全量池规模、达成状态 (`fulfilled` / `gap_detected`)、缺口数与第 N 名截断门槛线。
   - `records`：结构化数据记录预览。
   - `evidence_provenance`：数据来源治理信封（数据集 ID、版本、Profile 版本）。
3. **`QualityContext`（质量上下文）**：各关键维度的非空覆盖率、适用维度清单、数据完整度评分与预警。

### 2.2 标准输出契约 (`InsightPackage`)
Insight 组件标准输出严格由 **3 个信封/治理字段 + 8 个核心洞察块** 构成（11 个顶级字段，`additionalProperties: false` 严禁任何额外字段与执行命令）：

1. **`analysis_id`**：全局唯一分析追踪标识。
2. **`artifact_type`**：产物类型 (`InsightPackage` | `ElevationCandidate`)。
3. **`request_context`**：业务问题、意图类型、目标实体与范围。
4. **`conclusion`**：客观提炼的核心事实总结（带物理单位与配额达成描述）、置信等级（High/Medium/Low）、定性判断。
5. **`evidence`**：总样本量、有效样本量、表格预览与下钻键。
6. **`structure`**：产业经济学集中度建模（HHI、CR4、CR8、Gini、评级）、长尾分布结构、**目标配额达成评估 (quota_evaluation)**。
7. **`differences`**：跨群体同态对比、绝对与相对差值、**双样本显著性检验 (Mann-Whitney U + Cohen's d + Placebo 反驳检验)**。
8. **`anomalies`**：Tukey IQR 箱线与 Modified Z-Score 离群检测、多源指标冲突背离点。
9. **`quality_and_limits`**：数据完整度、覆盖率、质量警告与适用边界限制（Limitations）。
10. **`followups`**：建议继续追问的下一步分析方向（严格纯分析意图，0 业务执行命令）。
11. **`provenance`**：版本溯源与治理元数据（镜像自输入证据来源，组件版本 `1.3.0-rc3`）。

---

## 3. 核心功能规范 (Functional Specifications)

### 3.1 业务目标配额与优先级阶梯 (Quota Target & Priority Funnel)
- 支持业务人员输入目标容量（如 `选100家餐饮店`、`投放200台冰柜`）；
- 自动计算全量候选池规模（`pool_size`）与供给溢出比（如 `390.1:1`）；
- 自动提取入围最后一名（第 N 名）的关键指标门槛截断线（`cutoff_thresholds`）；
- 若严苛条件下有效样本不足，自动触发 `gap_detected`，并在追问建议中主动提供“放宽门槛弥补缺口”的灵敏度分析。

### 3.2 双轨极速执行架构 (Dual Engine Architecture)
- **主轨 (Vanna + DuckDB + SQL 静态防火墙)**：内存视图直读 62.9 万行 Parquet，毫秒级只读 SQL 过滤与聚合，性能提升 50~100 倍；
- **规则轨 (DSL + RuleQuery)**：处理多规则复合达标率打分排行；
- **确定性防火墙 (`firewall.py`)**：只读 SELECT 强校验、14 类高危指令拦截、防分号注入、强制注入 LIMIT 100,000 保护内存、字段白名单拦截。

### 3.3 统计质量与稳健性诊断 (Statistical Diagnostics)
- **显著性检验**：基于 `scipy.stats.mannwhitneyu` 输出 $p$ 值与 `Cohen's d` 效应量；
- **因果反驳检验 (Placebo Refuter)**：通过 $N=50$ 次随机化打乱标签模拟 Placebo 效应量分布，论证组间差异与离群结论的稳健性；
- **分布漂移检测**：基于 KS 检验与 Wasserstein 距离探测数据分布偏移。

### 3.4 智能多维研报生成器 (Data Storyboarding & Reporting)
- 支持一键生成深度 Executive 研报（如 *目标城市现磨咖啡竞争格局报告*、*目标区域便利渠道冰柜拓展研报*）；
- **目标自动规划**：基于 IEEE VIS 故事板理论将宏观目标自动拆解为 3~4 个递进章节；
- **确定性图表绑定**：基于 SIGMOD MultiVis-Agent 逻辑规则自动将数据绑定为横向条形图、环形饼图、分组柱状图或空间地图 (ECharts 5)；
- **独立导出交付**：生成包含全部交互图表、数据清单、执行摘要与限制声明的自包含 HTML 研报页面 (`output/reports/report_*.html`)。

---

## 4. 质量评估与验收指标达成

基于 50 题金标盲测集 (`references/golden_eval_dataset.json`) 与 5 维 Rubric 评测，PRD 核心指标实测达成如下：

| PRD 核心验收指标 | PRD 目标线 | RC3 实测达成值 | 达成判定 |
|---|---|---|---|
| **首次解释正确率** | **≥ 90.0%** | **100.0%** | ✅ **超额达标** |
| **业务专家认可率** | **≥ 80.0%** | **100.0%** | ✅ **超额达标** |
| **0 决策越界防守** | **100.0%** | **100.0%** | ✅ **完全达标** |
| **平均单题响应耗时** | **< 2.0s** | **0.41s** | ✅ **极速达标** |

---

## 5. 绝对禁止事项 (MUST NOT)

- **严禁生成业务执行命令**：绝对不生成 `Action`、`Task`、`RoutePlan`、`ScheduleDecision`、`CRMStateMutation` 或 `WorldStateWriteCommand`；
- **严禁替代业务决策**：严禁给出“应该派单”、“安排拜访”、“优先投放”等越界指导；
- **严禁自动修改世界状态**：分析主张为独立认识产物，严禁自动回写世界模型；
- **严禁伪造数据推断**：对缺失数据（如竞品在途销量）严格声明不可回答，严禁无数据推算。
