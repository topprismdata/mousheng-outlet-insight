# AGENT.md — outlet-insight 承接指南

> 面向：承接本项目的任何 Agent（人类或 AI）。读完本文件即可独立继续开发。
> 最后更新：2026-07-31 ｜ 当前状态：10 块能力已落地，回归 39/39 全绿

---

## 1. 一句话定位

**售点/潜客宽表（<n_rows> 行 × 152/<n_cols> 列）的自然语言查询智能体**：把业务黑话（"美宜佳"、"高评分"、"景区周边"）确定性地翻译成结构化 DSL 并在本地执行，零 LLM 依赖、编译期护栏防幻觉。项目目的是**提升自有 Agent 工程能力**，调研结论必须落地为代码增强，不停留在文档。

## 2. 统一项目目录（2026-07-31 起）

**一切都在 `~/outlet-insight/`**：

| 子目录 | 内容 |
|---|---|
| `skill/` | **代码 + SKILL.md + 本文件**（一切开发在这里；`~/.workbuddy/skills/outlet-insight` 是指向它的符号链接，技能照常加载） |
| `data/` | 底表 CSV/parquet/DM sqlite + `exports_89/`（N 份业务导出） |
| `docs/` | PRD（v3.6 权威版 + 历史版本）、开源调研、实例问题集、HTML 对比报告、`lint_prd.py` |
| `output/` | 全部分析报告 MD、查询结果 CSV（`sh_query_tests/`、`adhoc/`）、agent 产物 |

工作日志仍在 workspace：`~/WorkBuddy/2026-07-22-17-52-31/.workbuddy/memory/`（该 workspace 的 `output/`、`AGENT.md` 已改为指向本项目的符号链接）。

## 3. 数据与运行环境

- **底表**：`~/outlet-insight/data/<data>.csv`（<size> MB）；同名 `.parquet` 为缓存（<size> MB，自动生成）
- **DM 库**：`<data>.dm.sqlite`（数据同目录，`ensure_dm` 按 mtime 自愈重建）；早期实验产物 `~/outlet-insight/data/sh_dm.sqlite`
- **Python**：`/Users/ghb/.workbuddy/binaries/python/envs/store/bin/python`（pandas/pyarrow/datafusion 等已装）
- 数据特征：**100% 服务状态=未服务**（全是潜客）；省份=目标省份 <p>% + 目标城市 <p>% + 目标省份 <p>%；**"SH"≠目标城市**（是目标大区口径）；城市列存 **"目标城区"** 而非"目标城市"

## 4. 快速上手（先跑这三条）

```bash
PY=/Users/ghb/.workbuddy/binaries/python/envs/store/bin/python
cd ~/.workbuddy/skills/outlet-insight/scripts

$PY run_tests.py                 # 回归 39/39 应全绿（任何改动后必跑）
$PY agent_cli.py "目标子区域B的美宜佳清单"   # 单条 NL 查询
$PY dm_demo.py                   # DM 统计→点选→下钻 两步流演示（--repl 交互）
```

## 5. 架构：10 块能力与关键文件

| # | 能力 | 文件 |
|---|---|---|
| 1 | 会话态多步分析（工作集收缩，scope=current/full） | `session.py` |
| 2 | NL→DSL 解析（A 明确条件 / B 分布 / C 规则排行） | `nl_parser.py` |
| 3 | 语义层 + 编译校验（护栏：层外字段/非法算子执行前拒绝） | `agent_core.py` + `../references/field_registry.json` |
| 4 | 执行引擎（pandas 默认 / DataFusion 可选） | `engine.py` |
| 5 | 统计画像层（--profile，按业态覆盖率 + 缓存 + approx） | `profiler.py` |
| 6 | RAG 黄金查询记忆（本地 embedding，复用阈值 ≥0.82） | `memory.py` + `../references/query_memory.jsonl` |
| 7 | 洞察综合（InsightBench 式 summary/action/followups） | `insight.py` |
| 8 | MCP 服务（8 tool：query/validate/profile/…/stat/drilldown） | `mcp_server.py` |
| 9 | 导出模式扫描（N 份导出 A/B/C/D/E 分类 + E 两步走检测） | `extraction_pattern/` |
| 10 | **DM 汇总层 + 交互下钻**（B 类自动路由，24x 加速） | `dm_layer.py` + `dm_demo.py` |

辅助：`bizhours.py`（营业时间谓词）、`data_assertions.py`（漂移断言 T37）、`feedback_log.py`（W3C PROV 纠偏审计 T38）、`run_examples.py`。

**执行链路**：NL → `nl_parser.auto_parse` → DSL → `validate_query`（编译校验）→ `engine.run_query`（B 类先试 `dm_layer.route_b_query`，不可路由回退 pandas）→ 结果 + trace。

## 6. 铁律（改代码前必读）

1. **绝不静默丢过滤条件**。历史教训：province 不匹配被静默忽略返回全量。现约定：DM 路由遇到粒度外维度直接拒绝路由；MCP `stat` 返回 `ignored_filters` 警告；不存在的值返回 0 行而不是全量。
2. **护栏不放松**：任何新执行路径都必须先过语义展开 + `validate_query`，与主路径同构。
3. **零回归**：新能力默认关闭或自动回退，`run_tests.py` 39/39 必须保持全绿；新增能力要配新 T 用例。
4. **确定性**：同输入同输出，度量口径展开必须可复现（"高评分"→`>=3.8`，不许拍脑袋）。
5. **零 LLM 依赖**：解析与执行不调用大模型 API。

## 7. 已知 Gap / 待办（按优先级）

**P0 — domain_entities 缺口**（`references/field_registry.json`，多次实测暴露）：
- "零食很嗨" → `连锁品牌名称 contains`（当前误匹配到门店标签）
- "火锅" 等菜系 → `大众点评菜系 contains`（当前误判为连锁品牌名）
- "景区周边/景区附近" → `景区外50米/1公里/2公里` 三字段
- "连锁/直接服务/待开发" 口语词
- "目标城市"→"目标城区"：`GEO_NORM` 目前在 dm_layer 临时兜底，**应下沉到 field_registry 让 nl_parser 也吃到**

**P1**：多级 group_by（"按省份看渠道组分布"只聚一层）；条件互斥检测（"独立运营"+"国营"）；输出列选择（"需要有人数"）；C 类等级阈值 grade_high 0.5→0.75（当前 SH 底表上全"高"无区分度）。

**P2+**：见 PRD §11 路线图（策略规划层 FR-19、HIP 澄清 FR-26~30 等 22 项规划 FR）。

**数据语义注意**：`渠道组=教育` 指**学校周边店铺**而非学校本体；餐饮无真实开业年份字段（C 类用建档时间近似）。

## 8. 文档地图（项目根：`~/outlet-insight/`）

| 文档 | 说明 |
|---|---|
| `docs/outlet-insight_PRD.md` | **PRD v3.6**（权威需求文档：41 FR、12 NFR、路线图、数仓设计、HIP） |
| `skill/SKILL.md` | 使用手册：10 块能力用法 + 命令 + 关键文件表 |
| `output/售点洞察导出数据模式分析报告.md` | N 份业务导出的 A/B/C/D/E 模式分析（12 章） |
| `output/<data>` | SH 底表 13 章模式分析 |
| `output/7个业务场景查询测试报告.md` | 8 个业务场景（含 C 类）NL 查询测试 + gap 分析 |
| `output/base-data_业务查询模式复现测试报告.md` | 17 条查询复现（9 正确 8 失败根因） |
| `output/extraction_pattern_test_report.md` | 53 条 pytest 用例报告 |
| `output/sh_query_tests/` | 全部查询结果 CSV + `run_8_queries.py` 复现脚本 |
| `~/WorkBuddy/2026-07-22-17-52-31/.workbuddy/memory/2026-07-*.md` | 逐日工作日志（承接前建议倒序浏览最近 3 天） |
| `docs/开源Data_Agent生态研究*.md` | WrenAI/Cube/TaskWeaver/Vanna 调研（结论已落地进代码） |

## 9. 用户偏好

- 简体中文交流；结论直接、少客套
- 调研/分析必须转成可执行的代码增强，落到本 Skill；不要只产出文档
- 每个查询任务要求输出 CSV 结果文件（放 `~/outlet-insight/output/sh_query_tests/`）
- 完成实质工作后写工作日志到 workspace `.workbuddy/memory/YYYY-MM-DD.md`

## 10. 承接后第一步建议

1. 跑 `run_tests.py` 确认 39/39 基线
2. 读 SKILL.md 第 10 块 + PRD §5 FR 表，对齐当前能力边界
3. 从 §7 的 P0 domain_entities 缺口开始修（改 `field_registry.json` + 补 nl_parser 测试），这是投入产出比最高的活
