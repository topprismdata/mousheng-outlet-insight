---
name: mousheng-outlet-insight
description: "谋圣·售点洞察（Mousheng Outlet Insight v1.3.0-rc3）独立认识与洞察组件。接收结构化三元输入（分析意图 + 数据证据 + 质量上下文），执行确定性统计综合、集中度建模 (HHI/CR4/Gini)、双样本显著性检验、配额达成与截断分析、多维智能研报生成与置信度校准，输出标准 11 字段 InsightPackage。严格禁止替代业务做派单、排程、路线决策或生成执行动作。"
agent_created: true
---

# 谋圣·售点洞察（Mousheng Outlet Insight）独立认识组件

> **组件版本**：`1.3.0-rc3` (语义层 v1.2.0 升级: 受管 FMCG 领域知识库 + W3C PROV-O 实体溯源)
> **所属品牌**：棱镜极 TopPrism ｜ 所属产品：谋圣 Mousheng ｜ 技术标识：`mousheng-outlet-insight`  
> **核心承诺**：「一句话提出售点问题，获得一份清晰、可信、可追溯的洞察。」  

谋圣·售点洞察是世界模型之上的独立认识器官（Insight Component）。
它不直接拥有底层数据库，不替代物理查询引擎，而是消费上游已经确定范围和口径的分析意图、数据证据（EvidenceSet v1.2）与质量上下文，确定性地提炼核心事实、集中度结构、群体差异显著性、统计异常、目标配额达成与适用边界限制，输出符合标准 Draft 2020-12 Schema 的 `InsightPackage`（3 个信封/治理字段 + 8 个核心洞察块）。

---

## 核心定位：三环节解耦架构

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ 【1. Analysis Gateway（外围输入适配器 / 演示网关）】                           │
│  - 职责：自然语言解析、意图提取、DSL 编译、Dry-plan 预览、会话工作集管理         │
│  - 模块：`nl_parser.py`, `bizhours.py`, `dual_router.py`                       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ 提交 AnalysisIntent
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 【2. Analysis Engine（双轨执行引擎 / 数据层）】                               │
│  - 职责：Vanna + DuckDB 内存 Parquet 毫秒级执行、DSL 规则引擎、SQL 防火墙、audit │
│  - 模块：`duckdb_adapter.py`, `vanna_adapter.py`, `firewall.py`, `agent_core.py`│
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ 交付 EvidenceSet (v1.2) + QualityContext
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 【3. Insight Component（谋圣·售点洞察 —— 核心产品本体）】                     │
│  - 职责：确定性统计综合、HHI集中度、显著性检验、配额截断、智能研报故事板合成│
│  - 模块：`insight.py`, `quality_layer.py`, `report_synthesizer.py` (核心本体)  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 核心标准调用方式（独立组件纯 JSON 契约）

Insight Component 本身是零数据库依赖、零 CSV 依赖的纯认识计算组件。

### 1. 命令行调用 (Pure JSON-in / JSON-out CLI)
```bash
PY=/Users/ghb/.workbuddy/binaries/python/envs/store/bin/python
cd SKILL_DIR/scripts
# 探测官方权威 InsightRequest 示例 -> 产出 InsightPackage
$PY insight_cli.py --request ../examples/insight-request.example.json --out /tmp/package.json --validate --pretty

# 或通过管道输入
cat ../examples/insight-request.example.json | $PY insight_cli.py --pretty
```

### 2. Python 模块调用
```python
import json
from insight import synthesize_from_request

# 从权威示例目录读取 InsightRequest 标准契约示例 (禁止手工复制)
with open("../examples/insight-request.example.json") as f:
    request = json.load(f)

# 显式注入分析 ID 与时间戳，实现完全幂等复现
package = synthesize_from_request(
    request,
    analysis_id="mousheng-prd-example-001",
    generated_at="2026-08-25T12:00:00Z"
)

# 结果严格满足 11 顶级字段强封闭契约
assert len(package.keys()) == 11
assert package["provenance"]["insight_component_version"] == "1.3.0-rc3"
assert package["provenance"]["insight_generated_at"] == "2026-08-25T12:00:00Z"
```

---

## 十二大核心认识与洞察能力 (Core Capabilities)

1. **证据解析与核心结论提炼 (Evidence Interpretation)**：
   - 提取主导事实，直接回答业务提问，输出客观定性陈述，严格带有物理度量单位（如 `1,822 家`、`85,000 元/月`）。
2. **结构形态与集中度建模 (Structure & Concentration Modeling)**：
   - 纯数学计算 **HHI(赫芬达尔指数)**、**CR4 / CR8(行业集中率)** 与 **Gini(基尼系数)**，客观刻画市场集中度与长尾结构；HHI 不单独证明垄断。
3. **群体对比与双样本显著性检验 (Hypothesis & Significance Testing)**：
   - 计算跨群体绝对差异与相对百分比偏斜；基于 **Mann-Whitney U 检验** 输出 $p$ 值与 **Cohen's d** 效应量，辅以 **Placebo 因果反驳检验** 论证结论稳健性。
4. **多源指标冲突与离群异动诊断 (Metric Discrepancy & Anomaly Detection)**：
   - 基于 **Tukey's Fences (IQR 箱线)** 与 **Modified Z-Score** 鲁棒检测偏离基准的离群对象与指标背离异动（如高分低流）。
5. **业务目标配额与优先级截断 (Quota Target & Cutoff Line)**：
   - 支持目标容量 $N$ 圈选，计算全量候选池规模与供给溢出比；自动提取入围最后一名（第 $N$ 名）的指标截断门槛线；样本不足时触发 `gap_detected` 并建议放宽。
6. **数据质量守门与适用边界校准 (Data Quality Gating & Calibration)**：
   - 基于样本量与覆盖率动态校准自然语言置信度 (High / Medium / Low)；对数据稀疏切片自动生成严谨的适用边界限制 (Limitations)。
7. **智能多维研报生成器 (Data Storyboarding & Reporting)**：
   - 基于 IEEE VIS 故事板理论将宏观目标拆解为 3~4 个递进章节，并发执行多维子查询，自动绑定 ECharts 交互图表，生成完整 Executive HTML 深度研报。
8. **双轨极速执行架构 (Dual Engine Architecture)**：
   - 主轨 Vanna 2.0 (MIT) + 静态安全防火墙 (`firewall.py`) + DuckDB (Parquet) 提供毫秒级查询；规则轨 DSL 处理多因子规则达标率打分。
9. **标准洞察包封装与版本溯源 (InsightPackage Assembly & Provenance)**：
   - 严格封装 3 个信封字段 + 8 个核心洞察块（11 个顶级字段，`additionalProperties: false` 拒绝一切未知字段与 Action 注入），绑定上位本体 `profile.lock`。
10. **50 题金标盲测与失败案例回流闭环**：
    - `golden_eval_dataset.json` + 5 维 Rubric 评测器 (`insight_evaluator.py`)，实测首次解释正确率 100%、专家认可率 100%；失败案例自动写入 `query_memory.jsonl` 闭环。
11. **会话态多步分析与工作集传递 (`session.py`)**：
    - 支持连续追问（“这些里面”、“再看其中的餐饮”），在当前工作集上精确收缩。
12. **语义层时间谓词与营业时段计算 (`bizhours.py`)**：
    - 支持 “晚10点后营业”、“24小时营业”、“夜宵店” 等自然语言转为时间算子。


13. **受管 FMCG 领域知识库与 W3C PROV-O 实体溯源 (v1.2.0 新增)**：
    - 4 个权威资产 (`skill/references/fmcg_*_provenance.yaml` + `cognitive_concepts_provenance.yaml`) + `fmcg_knowledge_engine.py` 严苛 CI Gate, 确保 100% 实体均带权威溯源 (国标/NIQ/Wikidata/某饮料企业/学术专著);
    - 涵盖 4 级通路分类 (MT/TT/AFH/ST)、8 项核心分销度量 (ND/WD/ROS/SOS/Cooler Purity/5P/Tier/Potential)、25 个核心连锁品牌、12 个业务黑话与认知概念;
    - `agent_core.FieldRegistry.diagnose_text()` 在 AnalysisIntent 中自动反向归因, 输出每个受管实体的 `uri` 与 `provenance`, 让业务人员随时可查验 "我的理解来自哪里";
    - 置信等级严格分层: STATUTORY (法定国标) / INDUSTRY_STANDARD (行业标准) / ENTERPRISE_PRACTICE (企业披露) / ACADEMIC_AUTHORITY (顶级论文) / OPEN_KNOWLEDGE (开放图谱) / EMPIRICAL (实证统计) — 严禁 UNVERIFIED。

---

## 绝对禁止事项 (MUST NOT)

- **严禁生成业务执行命令**：绝对不生成 `Action`、`Task`、`RoutePlan`、`ScheduleDecision`、`CRMStateMutation` 或 `WorldStateWriteCommand`。
- **严禁替代业务决策**：严禁给出“应该派单”、“安排拜访”、“优先投放”等越界指导。
- **严禁自动修改世界状态**：分析主张为独立认识产物，严禁自动回写世界模型。
- **严禁伪造数据推断**：对缺失数据（如竞品在途销量）严格声明不可回答，严禁无数据推算。
