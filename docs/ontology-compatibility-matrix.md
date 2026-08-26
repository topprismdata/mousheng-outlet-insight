# 谋圣·售点洞察 本体 Profile 兼容性矩阵 (Ontology Compatibility Matrix)

**版本**：v1.0.0  
**生效日期**：2026-08-25  
**维护方**：棱镜极 TopPrism 架构治理委员会  
**权威存放路径**：`/Users/ghb/sh-store-insight/docs/ontology-compatibility-matrix.md`

---

## 1. 兼容性对应表

下表定义了 `mousheng-outlet-insight` 各组件版本与上位 `prism-ontology` 编译发布的 `outlet-insight.profile` 之间的严格兼容契约：

| 谋圣组件版本 (Component Ver) | 兼容上位本体版本 (Ontology Ver) | 兼容 Profile 版本 (Profile Ver) | 契约 Schema 版本 | 阶段状态 | 关键变更与兼容说明 |
|---|---|---|---|---|---|
| **v1.3.0-rc3** *(当前版本)* | `0.1.0` (Git: `9f35d2d`) | `0.1.0-rc2` | `draft-2020-12` | **Phase A/B/P0/P1/P2 全面封板** | 双轨执行引擎 (Vanna+DuckDB+SQL防火墙)、目标配额选点与截断分析、Evidence Semantics v1.2、scipy显著性与Placebo反驳、智能多维研报生成器。 |
| **v1.0.x** *(历史原型)* | N/A (本地字典) | N/A (未受管) | 自定义 JSON | 已废弃 (Deprecated) | 原技术探索原型 `sh-store-insight`，未接入受管 Profile。 |

---

## 2. 阶段验收状态定义

- **Phase 0B-A（物理盘点与候选语义分类）**：`PASSED`  
  - 152 物理列 100% 逐列覆盖，7 类语义模式分类完整，3 列歧义已隔离，1 缺失概念已声明。
- **Phase 0B-B（Operational Profile 语义绑定与门禁锁定）**：`PASSED`  
  - 上游 `prism-ontology` Profile v0.1.0-rc2 正式发布，105 个 URI 递归精确匹配，SHA-256 校验和 100% 通过，`profile.lock` 已原子生成。

---

## 3. 运行时兼容性校验规则 (Contract Gate)

当 `mousheng-outlet-insight` 启动或加载数据时，`OntologyProfileClient` / `test_profile_contract_gate.py` 必须按以下顺序执行硬性门禁校验：

1. **发布身份与一致性强校验**：
   - 检查 `ref_data["profile_version"] == manifest["version"]`、`ref_data["profile_uri"] == manifest["profile_uri"]`、`manifest["clean_working_tree"] is True`。
   - 校验 Git Tag 祖先可溯源性：`git merge-base --is-ancestor manifest_commit tag_commit`。
2. **SHA-256 Checksum 完整性校验**：
   - 读取 `references/profile.lock` 中的哈希值，与实际加载的 Profile 发行包 11 个文件逐一比对。若摘要不匹配，**立即抛出 `ProfileIntegrityError` 终止启动**。
3. **递归 URI 存在性精准匹配**：
   - 扫描 `dataset_mapping.yaml` 中所有 105 个 `prism:` URI，必须 100% 存在于上游受管注册表（Concepts, Relations, Metrics, Sources, Organizations）。
4. **SHACL 语义形状约束校验**：
   - 对数据映射中的核心实体、属性、值类型执行一致性断言。

---

## 4. 破坏性变更 (Breaking Changes) 处理策略

- **主版本升级 (Major Bump, e.g., 2.0.0)**：
  - 若 `prism-ontology` 修改了核心 `Outlet`、`OutletObservation` 实体继承结构或删除了关键度量，必须升级谋圣主版本并重新生成 `dataset_mapping.yaml`。
- **次版本升级 (Minor Bump, e.g., 1.1.0)**：
  - 允许在 Profile 中新增非破坏性实体分类（如新增子业态）或新增非强制性度量指标，谋圣保持向后兼容。
- **补丁版本升级 (Patch Bump, e.g., 1.0.1)**：
  - 仅限于词典别名修正、错误注释更新，下游重新锁定 `profile.lock` 即可。
