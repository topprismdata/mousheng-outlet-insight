# 谋圣·售点洞察 物理数据集与本体映射验证报告 (Mapping Validation Report)

**生成时间**：2026-08-25  
**数据集结构界定**：
- **物理底层 Schema (Physical Schema)**：152 列（真实底层 Parquet / CSV 物理列）
- **运行时分析 Frame (Runtime Analysis Frame)**：153 列（含加载时构建的运行时派生列）
- **治理映射记录 (Governance Mapping Records)**：153 条（152 条物理字段映射 + 1 条 `unavailable` 缺失业务概念声明）

**本体治理框架**：`prism-ontology` 三层架构（Reference Ontology → Operational Profile → Dataset Mapping）  
**上位 Profile 契约**：`outlet-insight-0.1.0-rc2` (Git Source Commit: `9f35d2d`, Dist Commit: `aab3e64`, Tag: `outlet-insight-v0.1.0-rc2`, CleanTree: `true`)  
**验证状态**：`VERIFIED_AND_LOCKED_AGAINST_PROFILE_0.1.0-RC2`（Phase 0 契约与工程管道双闭环已 100% 达成）  
**语义绑定状态**：`semantic_binding_status: validated`（全量 105 个唯一 URI 递归精确匹配上游 Profile 注册表）  
**关联配置文件**：`references/dataset_mapping.yaml` (v2.1.0, 已锁定) 与 `references/profile.lock`

---

## 1. 核心设计原则：看见并解释全部字段，但绝不生造 152 个本体概念

本体工程审视了宽表全部 152 列，以发现缺失的实体、关系、观察和指标语义；本体只吸收稳定概念，其余分别留在 Operational Profile、Dataset Mapping、Provenance 或隔离区。

---

## 2. 152 列物理宽表七类语义模式全量分布与验证结果

| 分类标识 (Field Class) | 涵盖物理列数 | 核心语义与本体模式 (Semantic Pattern) | 典型物理字段 | 语义绑定状态 | 存在性验证结果 |
|---|---|---|---|---|---|
| **1. `entity_identity` (核心实体身份与生命周期)** | 19 列 | 映射至 `prism:outlet/Outlet`、`prism:outlet/Brand`、`prism:outlet/CommercialSite` 的 `hasIdentifier`（声明 `TencentPOI` / `KO` / `CustomerAccountID` / `CtripPOI` 等 Scheme 方案），包含名称、物理地址以及实体生命周期（`EntityOnlineTime`, `RecordCreationTime`）。 | `腾讯售点id`, `KO_ID`, `客资编号`, `腾讯售点名称`, `POI上线时间`, `建档时间` | `validated` | `100% 精确存在` |
| **2. `classification_and_role` (分类与角色关系)** | 27 列 | 业态稳定分类（`ChannelType`）、品牌归属（`Brand`）以及特定企业组织在特定时间下的服务关系状态（`ServiceRelationship`，如 `服务状态=未服务` 属于时变业务关系而非网点固有物理属性）。 | `渠道组`, `子渠道`, `细分渠道`, `连锁品牌名称`, `服务状态`, `是否协议客户` | `validated` | `100% 精确存在` |
| **3. `spatial_and_territory` (空间与责任区域)** | 19 列 | **严格双轨制**：法定行政区域（`AdministrativeRegion` 省/市/区县，含 `GEO_NORM` 规范 URI）与企业销售辖区（`Territory` 规划大区/办事处/线路）物理隔离，加上场所商圈缓冲圈（`TradeAreaBuffer`）。 | `省份`, `城市`, `区县`, `规划大区`, `规划办事处`, `规划线路`, `景区外50米` | `validated` | `100% 精确存在` |
| **4. `external_observation` (外部平台客观观察与质检)** | 70 列 | 建模为带来源、时间与置信度的动态观测（`OutletObservation`, `DemographicObservation`, `AssetAndDisplayObservation`, `TownshipContext`），以及地址解析与腾讯 POI 匹配质量测量（`EntityResolutionAssessment`）。 | `大众点评餐饮点评评分星级`, `携程-综合评分`, `周末均值客流(人)`, `客资地址质量`, `客资标杆位置与匹配腾讯POI位置之间的距离（米）` | `validated` | `100% 精确存在` |
| **5. `derived_estimate` (派生推导与预测估计)** | 5 列 | 映射至 `DerivedEstimate`（含 3 类收入潜力预测、营业面积、NIQ指数），声明计算方法（`MachineLearningModel_v8` / `IndexCalculation`）与依赖特征（`based_on`），标注模型置信度，严禁被误当成既成客观事实。 | `月均收入潜力预测(无协议无冰柜)`, `月均收入潜力预测(如有冰柜无协议)`, `月均收入潜力预测(如有协议)`, `营业面积`, `NIQ-NARTD指数` | `validated` | `100% 精确存在` |
| **6. `ambiguous_isolated` (临时业务标签与歧义)** | 3 列 | 业务定义不清或临时实验标签，已配置 `excluded_from_execution`，声明 `candidate_meanings` 并要求治理，**阻断进入任何分析执行计划**。 | `待开发客户细分`, `是否匹配`, `Universe以外数据来源` | `isolated` | `100% 编译期阻断` |
| **7. `engineering_provenance` (数据工程与血缘溯源)** | 9 列 | 标记为 `not_applicable`，进入数据血缘（`DataEngineeringContext`）模型，不进入售点洞察业务分析。 | `备注`, `拜访日`, `拜访就近客户编码`, `OU回传数据_是否存在` | `not_applicable` | `100% 工程过滤` |
| **缺失业务概念 (`unavailable`)** | 1 概念 | 声明在本体中但物理表缺失的度量（`CompetitorSalesVolume` 竞品在途销量），配置 `output_explicit_limitation`。 | *(物理表缺失)* | `unavailable` | `100% 显式声明` |
| **合计** | 152 物理列 + 1 缺失概念 | **153 条全量记录，100% 逐列覆盖，0 未定义 URI** | | | |

---

## 3. 度量定义声明澄清

在上游 Profile (`metric-definitions.yaml`) 中共收录 **38 个受管度量定义**：
- **4 个派生结构指标**（`HerfindahlHirschmanIndex`, `Top4ConcentrationRatio`, `Top8ConcentrationRatio`, `GiniCoefficient`）具备明确的数学计算公式 (`formula`)；
- **34 个观测指标**（客流、评分、房间数、人口画像、排面占比等）具备清晰的业务口径定义（包含分子、分母或测量方式、适用业态、空间与时间范围、缺失值策略与解释边界），其余定性分类观测已准确归入 `relations.yaml` / 观测属性库。

---

## 4. 下游 Contract Gate 权威验证结论

通过运行 `scripts/test_profile_contract_gate.py` 自动化门禁流水线：
1. **发布身份与一致性强校验**：Manifest 版本等于引用版本 (`0.1.0-rc2`)，Git Source Commit (`9f35d2d`) 溯源通过，`clean_working_tree: true` 经物理断言通过。
2. **发行文件哈希校验**：上游 `0.1.0-rc2` 发行包中全部 11 个文件 SHA-256 校验和 100% 匹配。
3. **递归 URI 闭包校验**：本地提取的 105 个唯一 `prism:` URI 经集合运算全量存在于上游 Profile 注册表（32 个 Concepts, 64 个 Relations/Observed/Estimates, 38 个 Metrics, 7 个 Sources, 1 个 Organization），**未注册 URI 数量为 0**。
4. **原子锁定**：已通过 `tempfile + fsync + os.replace` 原子生成 `references/profile.lock`。

> **终验结论**：**Phase 0 语义契约与工程发布管道已正式实现 100% 闭环通过**。
