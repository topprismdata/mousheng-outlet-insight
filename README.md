<p align="center">
  <img src="https://raw.githubusercontent.com/topprismdata/.github/main/assets/brand/topprism-repo-header.png" alt="TopPrism dual-prism visual" width="100%" />
</p>

> **语言 / Language:** 中文为主 · English overview follows。
>
> **English overview:** Mousheng Outlet Insight is an independent, evidence-traceable insight component. It stops before dispatch, scheduling, routing, CRM writes, or channel decisions.

<div align="center">

# 谋圣·售点洞察

### Mousheng Outlet Insight

**一个把售点数据变成清晰、可信、可追溯洞察的独立认识组件。**
*An independent Insight component that turns outlet data into clear, trustworthy, traceable insights.*

</div>

## 1. 这是什么

**谋圣·售点洞察 (`mousheng-outlet-insight`)** 是棱镜极 TopPrism 公司谋圣 Mousheng 产品线下的**独立售点 Insight 组件**。它只做一件事——把售点数据和业务问题变成清晰、可信、可追溯的洞察,**不替用户做派单、排程、路线、CRM 写入或渠道决策**。

它的姐妹组件是 [`visit-scheduling-optimizer`](https://github.com/topprismdata/visit-scheduling-optimizer)——后者用 Operations Research (列生成 + CP-SAT) 做拜访排程优化,基于**本组件输出的 Insight** 去执行决策。两者的边界极清晰:

| 组件 | 职责 | 不做什么 |
|---|---|---|
| **`mousheng-outlet-insight` (本项目)** | 看懂并说清楚 | 不派单、不排程、不修改世界状态 |
| **`visit-scheduling-optimizer` (姐妹)** | 求解与执行排程 | 不做数据分析、不下业务结论 |

方法论论文支撑:[《基座模型平权时代独立数据服务商的壁垒重构》](docs/mousheng-outlet-insight_PRD.md) —— 通用大模型平权后,ISV 壁垒不在"写 SQL/生成代码",而在**把客户未在物理 Schema 显式编码的业务语义,翻译成结构化、确定性的机器逻辑**。本项目是该主张的**最小可运行实证**。

---

## 2. 三秒看懂:它如何工作

```
业务口语问句 ──→  [第一棱镜]  ──→  [中间光谱]  ──→  [第二棱镜]  ──→  11 字段 InsightPackage
               Insight Compiler  Insight Engine  Insight Synthesizer
               展开业务问题       完成可信分析    收束为可信洞察
```

**第一棱镜** (`agent_core` / `nl_parser`):把自然语言翻译为带 W3C PROV-O 溯源的确定性 DSL,编译期拦截非法字段与不兼容算子,杜绝幻觉。

**中间光谱** (`engine` / `mcp_server` / `dm_layer`):DuckDB + Vanna + 静态 SQL 防火墙,毫秒级执行,产出 EvidenceSet (v1.2) 与 QualityContext。

**第二棱镜** (`insight`):确定性统计综合,集中度建模 (HHI/CR4/Gini)、双样本显著性检验 (Mann-Whitney U + Placebo 反驳)、Tukey IQR 离群检测、目标配额达成与截断分析,输出标准 `InsightPackage`(3 个治理字段 + 8 个核心洞察块,11 个顶级字段,`additionalProperties: false`)。

---

## 3. 真实问句的完整链路示例

```text
用户问: "查一下夫妻老婆店的清单,优先看目标子区域A的"

  [第一棱镜 Insight Compiler]
    ├── 实体识别: "夫妻老婆店" ─→ prism:concept/MomAndPopStore
    │     溯源: GB/T 18106-2004 §3.2 食杂店定义 + 大众点评传统通路口语
    │     置信: STATUTORY (法定国标)
    ├── 区域识别: "目标子区域A" ─→ 字段 "规划大区"
    └── DSL 编译: {filters: [{渠道组=食杂, 是否连锁=否, 规划大区=目标子区域A}], order_by: [潜力 desc]}

  [中间光谱 Insight Engine]
    ├── DuckDB+Vanna 双轨执行 (毫秒级)
    ├── EvidenceSet v1.2 (显式度量单位 + 粒度)
    └── QualityContext (字段完整度 + 适用边界 + 配额达成)

  [第二棱镜 Insight Synthesizer]
    ├── 11 字段 InsightPackage (interpret + facts + structures + anomalies + ...)
    ├── action: 建议 (NOT 派单)
    └── followups: 建议下一步问句
```

业务人员随时可查验 "为什么把这些店定义为夫妻老婆店?" —— 见 AnalysisIntent 中的 PROV-O 溯源注记。

---

## 4. 六大核心产品能力

指导稿《谋圣·售点洞察产品定位与改进指导》§4 明确定义的产品边界(对外只讲这六项,内部技术模块退居幕后):

| # | 能力 | 说明 |
|---|---|---|
| 1 | **业务语义理解** | 字段别名、企业私有实体、品牌/菜系/业态识别、地理归一化、业务口径、时间表达、歧义检测与澄清 |
| 2 | **查询与探索** | 条件明细、分布统计、排名、区域与品牌比较、连续追问、汇总到门店下钻 |
| 3 | **规则评估** | 按用户规则评估候选对象,输出原始指标、每条规则结果、综合符合度、分层、数据完整度、规则版本与评估限制 |
| 4 | **洞察综合** | 头部对象、集中度、长尾结构、群体差异、排名变化、异常对象、指标冲突、数据覆盖差异、值得进一步验证的现象 |
| 5 | **质量与置信边界** | 数据版本、样本量、有效样本数、关键字段覆盖率、指标适用性、代理指标、异常值、时间边界、置信等级、不适合支持的结论 |
| 6 | **多形态交付** | 自然语言摘要、结构化 JSON、数据表、CSV、图表、区域地图、API 或 MCP ——共享相同数据、范围与口径 |

**绝对禁止**(`MUST NOT`):自动派单、自动决策、修改世界状态、伪造数据推断、生成业务执行命令(`Action` / `Task` / `RoutePlan` / `CRMStateMutation`)。

---

## 5. 受管知识库与 W3C PROV-O 溯源 (`v1.2.0` 升级)

本项目最具工业品质的部分:**每一个品牌、业态、度量、认知概念的定义都必须有权威出处**(国标 / 国际标准 / 顶级论文 / Wikidata / 企业公开披露)。**不靠大模型即兴编造,不靠人写零散规则。**

| 受管资产 | 实体数 | 权威源 |
|---|---|---|
| `fmcg_channel_taxonomy.yaml` | 13 个 4 级通路 (MT/TT/AFH/ST) | GB/T 18106-2021 · NIQ RMS · CCFA |
| `fmcg_metrics_provenance.yaml` | 8 项核心分销度量 (ND/WD/ROS/SOS/Cooler Purity/5P/Tier/Potential) | NIQ · RED (通用 RTM 框架) · 学术专著 |
| `fmcg_brand_registry.yaml` | 25 个 Top 连锁品牌 (含 Wikidata QID) | Wikidata · 企业公开披露 |
| `cognitive_concepts_provenance.yaml` | 11 个业务黑话与认知概念 (AliCoCo-style) | SIGMOD 2020 · 大众点评 · 饮料品牌 |

**严苛 CI Gate** (`scripts/fmcg_knowledge_engine.py`):
```text
[FMCG-KB][CI-GATE] 严苛溯源校验通过: 64 entities, 0 missing source.
```
**禁止任何** `UNVERIFIED` / `LLM_GENERATED_NO_SOURCE` 等级。**置信等级分层**:
- `STATUTORY` 法定国标 (GB/T)
- `INDUSTRY_STANDARD` 行业标准 (NIQ / CCFA / GS1)
- `ENTERPRISE_PRACTICE` 企业披露 (RED (通用 RTM 框架) / 宝洁 PJP / 企业年报)
- `ACADEMIC_AUTHORITY` 顶级论文 (SIGMOD / KDD / ISWC)
- `OPEN_KNOWLEDGE` 开放图谱 (Wikidata)
- `EMPIRICAL` 实证统计

---

## 6. 快速上手

### 0. 无数据即可验证(30 秒,验证 FMCG 知识库 CI)

```bash
cd skill/scripts        # 在本仓库根目录下执行

# 受管 FMCG 知识库严苛溯源 CI Gate (无需任何数据)
python3 fmcg_knowledge_engine.py ci

# 反向归因: 业务口语 -> 受管实体 + 权威溯源
python3 fmcg_knowledge_engine.py diagnose --query "查一下夫妻老婆店的清单"
```

### 1. 全量回归套件 (6 个 suite，作者机数据下 52/52；干净克隆下数据耦合用例如实记红)

```bash
python3 test_doc_examples.py                 # Phase A: 官方示例权威性      5/5
python3 -m pytest -v test_evidence_semantics.py     # Phase B: 证据语义增强     4/4
python3 -m pytest -v test_priority_quota.py          # Phase C: 业务目标配额     4/4
python3 test_insight_quality.py              # Phase D: 11 大黄金场景      11/11
python3 test_ci_eval_assertions.py           # Phase E: CI 门禁与越界词 19/19
python3 -m pytest -v test_fmcg_provenance.py       # Phase F: 受管 FMCG 溯源   9/9   ← v1.2.0 新增
```

### 2. 有数据时的自然语言查询(需先获取底表)

```bash
python3 agent_cli.py "目标子区域B的美宜佳清单"
python3 agent_cli.py "查一下夫妻老婆店的清单"
python3 agent_cli.py "餐饮街 ≥20 家 + 周末客流排序 + 评分 3.8+ 占比 50%+ 为高"
python3 dm_demo.py                           # DM 统计 + 交互下钻演示
```

### 3. 通过 MCP 暴露给其他 Agent

```bash
python3 mcp_server.py                        # stdio MCP, 暴露 9 个 tool
```

---
## 7. 数据接入

本组件是一个**纯计算型 Insight 组件**,**不内置任何样本数据**。当用户提供结构化的售点宽表 (CSV / Parquet) 时,组件自动发现 `data/` 目录下的数据文件并加载。底表由消费方按各自合规流程提供,组件本身不托管、不分发、不公开任何真实业务数据。

最小可运行子集(零数据):源码、`skill/scripts/`、`docs/`、`examples/`、受管 FMCG 知识库 5 个 YAML。

当数据接入后,执行 `engine.py` 会自动从 CSV 派生 Parquet 加速缓存,`dm_layer.py` 会按 mtime 自愈重建 DM 汇总层。组件对底表的字段命名与隐私边界保持完全中立 —— 不假设任何特定客户的字段、地区、品类体系,所有领域语义都通过受管知识库 (W3C PROV-O 溯源) 显式声明。

---

## 8. 目录结构

```text
~/outlet-insight/
├── README.md                                       ← 你正在看
├── AGENT.md                                        ← Agent 交接文档
├── .gitignore                                      ← 精细化排除大文件与缓存
├── skill/
│   ├── SKILL.md                                    ← 使用手册(13 项能力)
│   ├── scripts/                                    ← 核心实现
│   │   ├── fmcg_knowledge_engine.py                ← 受管 FMCG KB + 严苛 CI Gate (v1.2.0 新)
│   │   ├── insight.py / agent_core.py / agent_cli.py  ← 第二棱镜 / 核心 / CLI
│   │   ├── nl_parser.py / engine.py                ← 第一棱镜 NL→DSL → 执行
│   │   ├── session.py / memory.py                  ← 会话态 + RAG 黄金记忆
│   │   ├── profiler.py / dm_layer.py               ← 数据画像 / DM 汇总
│   │   ├── mcp_server.py                           ← MCP Server
│   │   ├── test_*.py                               ← 6 大测试套件 (52 项断言)
│   │   └── ...
│   └── references/
│       ├── fmcg_knowledge_base.yaml                ← 知识库元数据
│       ├── fmcg_channel_taxonomy.yaml              ← 4 级通路标准树
│       ├── fmcg_metrics_provenance.yaml            ← 分销度量溯源
│       ├── fmcg_brand_registry.yaml                ← Top 连锁品牌库
│       ├── cognitive_concepts_provenance.yaml      ← 业务黑话与认知
│       ├── field_registry.json                     ← 语义层 v1.2.0
│       ├── insight-package.schema.json             ← 11 字段 InsightPackage
│       ├── insight-request.schema.json
│       ├── ontology_profile_ref.yaml               ← 上位 prism-ontology Profile 引用
│       └── ...
├── docs/
│   ├── mousheng-outlet-insight_PRD.md              ← PRD v1.3.0-rc3 权威
│   ├── ontology-compatibility-matrix.md            ← 兼容矩阵
│   ├── designs/fmcg-knowledge-base/                ← 设计沉淀镜像
│   ├── 开源Data_Agent生态研究*.md                  ← WrenAI/Cube/TaskWeaver/Vanna 调研
│   ├── 售点洞察实例问题集.md
│   ├── archive/                                  ← 历史 PRD 版本 v2.9.bak/v3.0/v3.1/v3.2(已归档,仅作追溯,政策见 archive/README.md)
│   └── ...
├── examples/                                       ← 权威 Insight Package 契约示例
├── demo/                                           ← 浏览器交互 demo (HTML+Flask)
└── data/                                           ← 大文件,不提交 (见 §7)
```

---

## 9. 技术栈与依赖

| 类别 | 选型 | 用途 |
|---|---|---|
| 语言 | Python ≥ 3.10 | 主语言 |
| 引擎 | DuckDB 1.5.5 + Vanna 2.0.2 | 双轨执行引擎 (毫秒级 Parquet 查询) |
| 统计 | scipy | Mann-Whitney U 显著性 + Tukey IQR 离群 |
| 知识本体 | W3C PROV-O + ISO 11179 | 实体定义与溯源 |
| 图表 | ECharts (Flint spec) | 多维研报可视化 |
| 接口 | MCP (stdio) | 让别的 Agent 调用本组件 |

---

## 10. 验证与质量

**当前状态**：`v1.3.0-rc3`（语义层 v1.2.0）· 回归套件在**作者本机数据 + 专用解释器**下全绿；**干净克隆**（无 `data/` 私有 CSV）下，绑定真实数据内容的用例会被如实记为失败（不再崩溃）——见运行前提。

> **运行前提**：全量套件依赖未入库的 `data/*.csv`（PII，已 gitignore）。缺失时 `find_default_data()` 回退并如实报告；部分用例断言作者私有数据的具体行数值（如 `目标子区域B/37/346`），换数据必然不绿，属预期而非缺陷。需装依赖（pandas/shapely/jsonschema）的解释器，`python3` 裸环境会 import 失败。

| 套件 | 项数 | 状态 |
|---|---:|:---:|
| Phase A: 官方示例权威性 | 5 | ✅ |
| Phase B: 证据语义增强 | 4 | ✅ |
| Phase C: 业务目标配额 | 4 | ✅ |
| Phase D: 11 大黄金场景洞察质量 | 11 | ✅ |
| Phase E: CI 门禁与越界词 | 19 | ✅ |
| Phase F: 受管 FMCG 知识库溯源 | 9 | ✅ |

---

## 11. 命名约定(避免与历史代号混淆)

```
?镜极 TopPrism                          (公司/主品牌 · 双?镜方法论)
  └── 谋圣 Mousheng                     (客户产品体系)
        ├── 售点洞察 Outlet Insight     (本组件,识别并提炼洞察)
        │     └── mousheng-outlet-insight   (技术标识 / GitHub repo)
        └── 拜访排程 Visit Scheduling Optimizer (姐妹组件,做排程决策)
              └── visit-scheduling-optimizer   (技术标识 / GitHub repo)

数据域标识 (data domain identifier, 不是产品名):
        └── outlet-data-domain  (SH 代表目标区域售点数据域, 仅用于 data/ 文件命名)
```

历史研发代号 `outlet-insight` 已降级为**数据域标识**(代表目标区域售点数据域),**不再作为产品主名称**。新的业务讨论与代码注释请使用 `mousheng-outlet-insight` / `谋圣·售点洞察` / `Mousheng Outlet Insight`。

---

## 12. 引用与致谢

- **方法论文**:`《基座模型平权时代独立数据服务商的壁垒重构》`
- **指导稿**:`《谋圣·售点洞察产品定位与改进指导》v1.1`
- **生态调研**:WrenAI / Cube / TaskWeaver / Vanna / ktx(见 `docs/开源Data_Agent生态研究*.md`)
- **本体研究**:PROV-O · SOSA/SSN · OBO Foundry · FIBO · AliCoCo (SIGMOD 2020) · AutoKnow (KDD 2020) · LLMs4OL (ISWC 2024/2025)
- **FMCG 知识源**:GB/T 18106-2021 (零售业态) · GB/T 4754-2017 (国民经济行业) · NIQ RMS · GS1 GPC · CCFA · RED (通用 RTM 框架) · 宝洁 PJP · Wikidata · 大众点评

---

## TopPrism status

| Field | Value |
|---|---|
| Purpose | Customer Decision · Insight Component |
| Maturity | Applied component; public implementation and regression evidence |
| Evidence | Governed FMCG knowledge provenance and repository regression suites; no customer-deployment claim |
| Boundary | Insight only; no dispatch, scheduling, route assignment, CRM write or automatic channel decision |
| Related | `prism-ontology`, `visit-scheduling-optimizer` |

---

## 13. 许可证

内部项目 (Internal)。请联系棱镜极 TopPrism 获取使用授权。

---

<div align="center">

**谋圣·售点洞察 · 只负责看懂并说清楚。**
*World model on top. Mousheng Outlet Insight below.*

</div>
