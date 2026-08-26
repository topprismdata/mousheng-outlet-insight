# AGENT.md — 谋圣·售点洞察 (mousheng-outlet-insight) 承接指南

> **面向**：承接本项目的任何 Agent（人类或 AI）。读完本文件即可独立继续开发。  
> **所属品牌**：棱镜极 TopPrism ｜ 所属产品：谋圣 Mousheng ｜ 技术标识：`mousheng-outlet-insight`  
> **当前版本**：`1.3.0-rc3` (语义层 v1.2.0 升级: 受管 FMCG 领域知识库 + W3C PROV-O 实体溯源)
> **当前状态**：双轨引擎换血完毕（Vanna+DuckDB+SQL防火墙），50 题金标盲测 100% 通过（首次解释正确率 100% / 专家认可率 100%），智能多维研报生成器上线，全量回归 165/165 全绿。

---

## 1. 一句话定位

**世界模型之上的独立认识组件（Insight Component）**：接收分析意图（AnalysisIntent）、数据证据（EvidenceSet v1.2）与质量上下文（QualityContext），确定性地提炼结论、证据、集中度结构、显著性差异、离群异常、配额达成与适用边界限制，输出标准 11 顶级字段 `InsightPackage` 并支持一键生成多维 Executive 分析研报。严禁替用户做派单、排程、路线或 CRM 写入操作。

---

## 2. 统一项目目录与核心模块地图

**项目根目录：`~/sh-store-insight/`**

| 子目录 | 内容 | 关键模块与文件 |
|---|---|---|
| `skill/scripts/` | **核心代码与适配器** | · `insight.py` (核心第二棱镜合成器，11 顶级字段封装)<br>· `dual_router.py` (双轨路由器：Vanna+DuckDB 主轨 / DSL 规则轨)<br>· `duckdb_adapter.py` (DuckDB 内存 Parquet 直读，毫秒级执行)<br>· `vanna_adapter.py` (Vanna 2.0 语义转译与黄金 SQL 知识库)<br>· `firewall.py` (SQL 确定性只读防火墙与注入拦截)<br>· `quality_layer.py` (scipy 双样本显著性 + Placebo 反驳检验)<br>· `report_planner.py` (分析目标规划器，多任务拆解)<br>· `chart_adapter.py` (ECharts 5 规则化图表适配器)<br>· `report_synthesizer.py` (Executive 叙事研报合成器)<br>· `trace_logger.py` (OpenTelemetry 兼容全链路追踪)<br>· `feedback_integration.py` (失败案例自动回流闭环) |
| `skill/references/` | **受管语义与契约底座** | · `field_registry.json` (153 列物理映射 + 私有实体 + 歧义隔离)<br>· `profile.lock` (105 个受管 URI 原子锁定)<br>· `insight-request.schema.json` (三元输入 Draft 2020-12 Schema)<br>· `insight-package.schema.json` (输出强封闭 Draft 2020-12 Schema)<br>· `golden_eval_dataset.json` (50 题金标评估数据集与 Rubric) |
| `demo/` | **Agentic 交互前端** | · `server.py` (本地服务，提供 8 个 API 端点)<br>· `index.html` (上海 16 区县矢量着色地图 + 折叠轨迹栏 + 研报生成弹窗) |
| `output/` | **评估产物与研报** | · `reports/` (生成的自包含交互式 HTML 研报)<br>· `all_tested_query_scenarios.csv` (全量 69 条测试场景 12 列清单)<br>· `blind_eval_report_v1.md` (50 题金标盲测评估报告)<br>· `quality_report.html` (数据质量与统计画像体检报告) |

---

## 3. 数据与运行环境

- **底表**：`data/SH全量售点洞察1784527757315.parquet`（122MB，629,074 行 × 153 列华东全量潜客大宽表）
- **Python**：`/Users/ghb/.workbuddy/binaries/python/envs/store/bin/python`（DuckDB 1.5.5, Vanna 2.0.2, scipy, ECharts 已就绪）
- **数据核心特征**：100% 服务状态=未服务（全是潜客）；省份=江苏 51.7% + 上海 37.6% + 浙江 10.7%；"SH"≠上海（中国华东大区口径）；城市列存 "上海城区" 而非 "上海市"

---

## 4. 快速验证命令（先跑这三条）

```bash
PY=/Users/ghb/.workbuddy/binaries/python/envs/store/bin/python
cd ~/sh-store-insight

# 1. 运行自动化 CI 安全门禁与越界词断言 (19/19 必须全绿)
$PY skill/scripts/test_ci_eval_assertions.py

# 2. 运行 50 题长尾盲测集测试 (50/50 必须全绿)
$PY skill/scripts/blind_test_suite.py

# 3. 运行 11 大黄金场景洞察质量测试 (11/11 必须全绿)
$PY skill/scripts/test_insight_quality.py

# 4. 运行受管 FMCG 知识库溯源合规测试 (9/9 必须全绿) - W3C PROV-O 强制
$PY -m pytest -v skill/scripts/test_fmcg_provenance.py

# 5. 启动 FMCG 知识库严苛 CI Gate (64 entities, 0 missing source)
$PY skill/scripts/fmcg_knowledge_engine.py ci
```
```

---

## 5. 核心铁律（改代码前必读）

1. **严禁业务执行越界 (MUST NOT)**：绝对不生成 Action/Task/RoutePlan 实体，严禁给出派单、路线、排程建议。
2. **严禁自动修改世界状态**：分析主张为独立认识产物，严禁自动回写世界模型。
3. **绝不静默丢过滤条件**：歧义字段显式隔离拦截（`excluded_from_execution`），未建模条件进入 `ambiguities`。
4. **确定性与零随机性**：洞察合成与集中度评估纯基于数学统计与规则，同输入 100% 产出同输出。
5. **版本一致性**：组件版本统一锁定为 `1.3.0-rc3`，权威示例 `examples/` 必须保持字节级配对一致。
6. **实体定义必须有溯源**：v1.2.0 起，任何实体（品牌/业态/认知概念/度量）的定义都必须带 `uri` + `provenance.authority_ref` + `provenance.confidence_tier`。禁止 UNVERIFIED/LLM_GENERATED_NO_SOURCE 等级。CI Gate 失败必须就地修复，严禁隐式放宽。
