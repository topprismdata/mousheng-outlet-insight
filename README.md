# 谋圣·售点洞察 (mousheng-outlet-insight)
> 
> **售点/潜客宽表（629,074 行 × 152 列）自然语言查询智能体** —— 技能、文档、数据的唯一家目录。
> **项目根目录**：`~/sh-store-insight/` ｜ **品牌**：棱镜极 TopPrism ｜ **技术标识**：`mousheng-outlet-insight`
> **当前版本**：`1.3.0-rc3` (语义层 v1.2.0) ｜ 整合日期：2026-07-31 (v1.2.0 FMCG 知识库升级：2026-08-26)
>
> **⏬ 数据获取**：底表 `SH全量售点洞察*.csv` (584MB) / `.parquet` (122MB) / `.dm.sqlite` 与 89 份业务导出
> 受 `.gitignore` 排除，请从【某饮料企业内网 OneDrive / 项目 Wiki / 业务导出库】获取后放入 `data/` 目录。
> 最小可运行子集（不含大数据）：源码、Skill 脚本、docs/、examples/、受管 FMCG 知识库 即可开箱即用。
> 整合日期：2026-07-31

## 目录结构

```
~/sh-store-insight/
├── README.md            ← 本文件（导航）
├── AGENT.md             ← 交接文档（新 agent 从这里开始读）
├── skill/               ← 技能实体（代码 + SKILL.md + references）
│   │                       ~/.workbuddy/skills/sh-store-insight 是指向这里的符号链接
│   ├── SKILL.md            使用手册（10 块能力）
│   ├── scripts/            agent_cli / nl_parser / engine / session / dm_layer / mcp_server / run_tests ...
│   └── references/         field_registry.json 语义层
├── data/                ← 全部数据
│   ├── SH全量售点洞察1784527757315.csv       底表（557M）
│   ├── SH全量售点洞察1784527757315.parquet   加速缓存（117M）
│   ├── SH全量售点洞察1784527757315.dm.sqlite DM 汇总层（自愈重建）
│   ├── sh_dm.sqlite                          早期 DM 实验产物
│   └── exports_89/                           89 份业务人员导出（162M）
├── docs/                ← 文档
│   ├── sh-store-insight_PRD.md               PRD v3.6（权威）
│   ├── sh-store-insight_PRD_v*.md            历史版本
│   ├── 开源Data_Agent生态研究*.md            WrenAI/Cube/TaskWeaver/Vanna 调研
│   ├── 售点洞察实例问题集.md
│   ├── *.html                                能力对比 / 论文对照报告
│   └── lint_prd.py                           PRD 一致性检查脚本
└── output/              ← 全部分析报告与查询结果
    ├── 售点洞察导出数据模式分析报告.md        89 份导出 A/B/C/D/E 模式（12 章）
    ├── SH全量售点洞察_模式分析报告.md         SH 底表 13 章分析
    ├── 7个业务场景查询测试报告.md             8 场景 NL 测试 + gap
    ├── SH底表_业务查询模式复现测试报告.md     17 条查询复现
    ├── extraction_pattern_test_report.md      53 条 pytest 报告
    ├── sh_query_tests/                        查询结果 CSV + run_8_queries.py
    ├── adhoc/                                 散查询 CSV
    └── agent/ results/ ...                    早期 agent 产物
```

## 快速上手

```bash
PY=/Users/ghb/.workbuddy/binaries/python/envs/store/bin/python
cd ~/sh-store-insight/skill/scripts

$PY run_tests.py            # 回归基线 39/39
$PY agent_cli.py "浦西大区的美宜佳清单"   # NL 查询
$PY dm_demo.py              # DM 统计 + 交互下钻演示
```

## 关联位置

- **技能加载入口**：`~/.workbuddy/skills/sh-store-insight` → 符号链接到 `./skill`
- **工作日志**：`~/WorkBuddy/2026-07-22-17-52-31/.workbuddy/memory/`（workspace 的 `output/`、`AGENT.md` 已改为指向本项目的符号链接）
- **数据原件备份**：`~/Downloads/` 保留一份底表与 89 份导出原件（本项目 `data/` 为工作副本，代码默认读这里）
