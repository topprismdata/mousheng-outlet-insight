# 谋圣·售点洞察 本体 Profile 兼容性矩阵 (Ontology Compatibility Matrix)

**版本**：v1.1.0
**更新日期**：2026-09-06
**维护方**：棱镜极 TopPrism 架构治理委员会
**存放路径**：`docs/ontology-compatibility-matrix.md`（仓库相对路径；上游发行目录解析优先级：环境变量 `PRISM_DIST_DIR` → 仓库同级 `../prism-ontology/dist/outlet-insight/<profile_version>` → `~/prism-ontology/dist/outlet-insight/<profile_version>`，禁止硬编码机器绝对路径）

---

## 1. 兼容性对应表

下表定义了 `mousheng-outlet-insight` 各组件版本与上位 `prism-ontology` 编译发布的 `outlet-insight.profile` 之间的严格兼容契约：

| 谋圣组件版本 (Component Ver) | 兼容上位本体版本 (Ontology Ver) | 兼容 Profile 版本 (Profile Ver) | 契约 Schema 版本 | 阶段状态 | 关键变更与兼容说明 |
|---|---|---|---|---|---|
| **v1.3.0-rc3** *(当前版本)* | `0.1.0-rc2`（tag `outlet-insight-v0.1.0-rc2`，dist commit `417cb65`，source commit `f8fa1d2`） | `0.1.0-rc2` | `draft-2020-12` | **已锁定，但已被上游 rc3/rc4 超越（迁移待办，见第 4/5 节）** | 双轨执行引擎 (Vanna+DuckDB+SQL防火墙)、目标配额选点与截断分析、Evidence Semantics v1.2、scipy显著性与Placebo反驳、智能多维研报生成器。注意：`profile.lock` 生成于脱敏提交（`8c87170`/`dbe0a2f`）之前，当前映射对 rc2 复跑门禁即失败（3 个脱敏改名 URI 未注册），见第 5 节。 |
| v1.3.0-rc3（同上，未升级） | `0.1.0-rc3`（tag `outlet-insight-v0.1.0-rc3`，dist commit `45f871c`，source commit `dc42efa`；后由 `f2cc885` 自 `b548880` 重建 dist） | `0.1.0-rc3` | `draft-2020-12` | **上游已发布，下游未锁定（门禁阻断）** | 引入 `field-mapping.yaml`（`mapping_only` 注释层，见第 3.1 节）；核心受管关系注册表由 64 条收缩至 38 条（物理列降级条目剥离至映射层）；删除孤儿溯源概念 `prism:provenance/DataEngineeringContext`；GOVERNANCE 升至 v0.2。SHACL 仍使用 `observedValue`（破坏性更名尚未发生）。 |
| v1.3.0-rc3（同上，未升级） | `0.1.0-rc4`（tag `outlet-insight-v0.1.0-rc4`，dist commit `845f92f`，source commit `9825c72`） | `0.1.0-rc4` | `draft-2020-12` | **上游最新已发布，下游未锁定（门禁阻断 + SHACL 破坏性变更）** | 在 rc3 基础上：① **SHACL 破坏性更名 `prism-core:observedValue` → `prism-core:hasResultValue`**（含约束消息文案，见第 4.1 节）；② 新增 `prefix-map.json` + `context.jsonld` 机器可读 CURIE 前缀契约（9 个前缀，见第 3.2 节）；③ 新增 3 个防混淆 SHACL 形状（`RouteStructureShape`/`RouteVsPlanShape`/`SalesRepVsAccountShape`）；④ 3 个渗透率类度量分母口径收紧为"在册品牌网点总数（剔除无牌独立店）"；⑤ OWL `versionInfo` 首次与发行号同步（rc1–rc3 的 OWL 均滞留 `0.1.0-rc1`）。 |
| **v1.0.x** *(历史原型)* | N/A (本地字典) | N/A (未受管) | 自定义 JSON | 已废弃 (Deprecated) | 原技术探索原型 `outlet-insight`，未接入受管 Profile。 |

> **上游两阶段发布流程（rc3/rc4 均遵循，下游门禁据此溯源）**：先落源码提交（`feat:`/`refactor:`/`docs:`），再落发行提交（`release(dist): build <tag> from <source-sha>`）并打 tag `outlet-insight-v0.1.0-rcN`（指向 dist 提交）；`profile-manifest.json` 的 `git_commit` 记录**源码提交**，下游门禁以 `git merge-base --is-ancestor <manifest_commit> <tag_commit>` 强制校验祖先关系。以 rc4 为例：source `9825c72` → dist/tag `845f92f`。

---

## 2. 阶段验收状态定义

- **Phase 0B-A（物理盘点与候选语义分类）**：`PASSED`
  - 152 物理列 100% 逐列覆盖，7 类语义模式分类完整，3 列歧义已隔离，1 缺失概念已声明。
- **Phase 0B-B（Operational Profile 语义绑定与门禁锁定）**：`PASSED @ rc2`（2026-08-25，commit `39deda9`；**该结论已被后续脱敏提交部分失效，见第 5 节**）
  - 上游 `prism-ontology` Profile v0.1.0-rc2 正式发布，105 个 URI 递归精确匹配，SHA-256 校验和 100% 通过，`profile.lock` 已原子生成。
  - 2026-09-06 复核：rc2 发行包 11 个文件 SHA-256 仍全部匹配，锁身份字段有效；但当前 `dataset_mapping.yaml` 含 3 个上游从未注册的脱敏改名 URI，门禁复跑失败，锁处于"不可再生"状态（`profile.lock` 的 `reproducibility_note`）。

---

## 3. 运行时兼容性校验规则 (Contract Gate)

当 `mousheng-outlet-insight` 启动或加载数据时，`OntologyProfileClient` / `test_profile_contract_gate.py` 必须按以下顺序执行硬性门禁校验：

0. **上游发行目录可移植解析**：
   - 优先级：环境变量 `PRISM_DIST_DIR` → 仓库同级 `../prism-ontology/dist/outlet-insight/<profile_version>` → `~/prism-ontology/dist/outlet-insight/<profile_version>`；禁止机器绝对路径。
   - 仅在按优先级尝试全部候选后仍不可达时，pytest 入口以 skip-with-message 跳过；其余任何断言失败一律硬失败。
1. **发布身份与一致性强校验**：
   - 检查 `ref_data["profile_version"] == manifest["version"]`、`ref_data["profile_uri"] == manifest["profile_uri"]`、`manifest["clean_working_tree"] is True`。
   - 校验 Git Tag 祖先可溯源性：`git merge-base --is-ancestor manifest_commit tag_commit`（两阶段发布溯源，见第 1 节说明）。
2. **SHA-256 Checksum 完整性校验**：
   - 读取上游 `checksums.sha256` 逐一比对真实文件哈希（rc2 为 11 个受管文件；rc4 增至 14 个，新增 `field-mapping.yaml`、`prefix-map.json`、`context.jsonld`），并断言 `profile-manifest.json` 的 `included_files` 与校验清单完全一致。若摘要不匹配，**立即终止，不得锁定**。
3. **field-mapping 注释层消费（rc3+）**：
   - 发行包存在 `field-mapping.yaml` 时，逐条断言其 `governance_status == "mapping_only"`；条目 URI 作为**独立的映射层引用集合**放行，**绝不并入本体概念/关系注册表**（防概念膨胀）。
4. **递归 URI 存在性精准匹配**：
   - 扫描 `dataset_mapping.yaml` 中所有 unique `prism:` URI（当前 105 个），按类别（metric/observed/estimate/source/org/lifecycle/其余）执行精确集合成员校验，禁止模糊前缀；`mapping_only` 注释层 URI 独立放行。
5. **SHACL 语义形状约束校验**：
   - 对数据映射中的核心实体、属性、值类型执行一致性断言；rc4 起注意 `hasResultValue` 更名（第 4.1 节）。

### 3.1 field-mapping `mapping_only` 语义（rc3+）

`field-mapping.yaml` 是从核心受管关系注册表**剥离**出的物理列降级映射层（`governance_status: mapping_only`，条目 `definition_pending: true`）。语义约定：

- 这些 URI（如 `prism:observed/是否高德扫街榜/状元榜`、`prism:estimate/RevenuePotentialWithAgreement`）是**合法的映射层引用**，下游映射可继续使用；
- 它们**不是本体概念/关系**，不得进入概念注册表、不得参与本体推理或作为受管语义节点引用；
- rc4 的 field-mapping 层含 44 个条目；下游 105 个映射 URI 中有 43 个依赖该层放行。

### 3.2 机器可读 CURIE 前缀契约（rc4 新增）

- `prefix-map.json`：`"prism:core/" → "prism://ontology/core/"` 等 9 组 CURIE 前缀 ↔ 规范 RDF 命名空间映射（draft 2020-12 schema 标注）；
- `context.jsonld`：等价语义的 JSON-LD `@context`（`prism-core`、`prism-outlet`、`prism-sales`、`prism-insight`、`prism-quality`、`prism-lifecycle`、`prism-metric`、`prism-source`、`prism-profile`）；
- 下游 CURIE 展开**必须**改由该契约驱动，不得再各自硬编码命名空间表（当前门禁尚未强制校验该文件，属后续加固项）。

---

## 4. 破坏性变更 (Breaking Changes) 处理策略

### 4.1 rc4：SHACL 属性更名 `observedValue` → `hasResultValue`（破坏性）

- 变更内容：`prism-sales` 观测形状约束 `sh:path prism-core:observedValue` → `sh:path prism-core:hasResultValue`（约束消息同步改为 `hasResultValue`）。rc3 及更早发行仍为 `observedValue`，该更名**一次性、不可回退**。
- 影响面：任何**构造或校验观测 RDF**的下游 fixture/代码/样例数据，凡写入 `prism-core:observedValue` 谓词者，升级 rc4 后 SHACL 校验将全部失败。
- 下游现状（2026-09-06 排查）：`skill/` 与 `tests/` 内**无任何** `observedValue` 代码引用（仅本文档与本矩阵记录），因此当前无立即破损点；**残留迁移义务**为：未来任何 RDF 构造层、样例数据与 SHACL 校验入口必须直接采用 `prism-core:hasResultValue`，并在 rc4 重锁前补充观测值谓词的一致性用例。
- 迁移方式：全局替换谓词 → 重跑 SHACL 校验 → 门禁重锁。

### 4.2 其他破坏性/口径变更

- **上游 URI 删除（rc3 起）**：`prism:provenance/DataEngineeringContext` 自 rc3 起从概念注册表删除，下游映射引用它将直接被门禁拒绝，必须重映射或降级为工程注记。
- **度量分母口径收紧（rc4）**：3 个渗透率/份额类度量分母改为"区域业态全部在册品牌网点总数（剔除无牌独立店）"，下游指标计算与校验基准需同步对齐。
- **主版本升级 (Major Bump, e.g., 2.0.0)**：若 `prism-ontology` 修改了核心 `Outlet`、`OutletObservation` 实体继承结构或删除了关键度量，必须升级谋圣主版本并重新生成 `dataset_mapping.yaml`。
- **补丁版本升级 (Patch Bump, e.g., 1.0.1)**：仅限于词典别名修正、错误注释更新，下游重新锁定 `profile.lock` 即可。

---

## 5. 当前门禁复核结论（2026-09-06，勿删）

以仓库约定的 store 虚拟环境解释器执行 `python -m pytest skill/scripts/test_profile_contract_gate.py -q` 实测：

- **对 rc4**：身份校验 ✓、tag 祖先 ✓、14/14 SHA-256 ✓、field-mapping 44 条 `mapping_only` 消费 ✓，随后 **URI 集合断言失败（4 个未注册）**：
  `prism:observed/示范企业乡镇主数据`、`prism:org/示范饮料集团`、`prism:provenance/DataEngineeringContext`、`prism:source/BeverageTownshipMaster`。
- **对 rc2**：同样失败，但仅 **3 个未注册**（除 `DataEngineeringContext` 外的上述 3 个）。
- 根因：这 3 个 URI 是脱敏提交 `8c87170`/`dbe0a2f` 对**上游已注册 URI** 的下游改名——`org`/`source`/`observed` 三类上游 URI 真名含客户企业标识（依脱敏政策不复述）→ 改为中性名 `示范饮料集团`/`BeverageTownshipMaster`/`示范企业乡镇主数据`；任何上游发行（rc1–rc4）均未注册脱敏别名。
- 结论：**当前没有任何上游发行能让门禁通过**，`profile.lock` 保持 rc2（最后一个真实生成的锁；发行包字节级未变，11 项哈希 2026-09-06 复验全部匹配），并在锁内以 `reproducibility_note` 标注不可再生状态。**不得**为使门禁变绿而弱化精确集合校验或伪造别名注册。

**rc4 重锁前置清单（下游待办）**：
1. 解决脱敏别名与上游注册表的一致性（上游注册别名，或经治理批准的显式别名解析层）；
2. 重映射或降级 `prism:provenance/DataEngineeringContext`；
3. 按第 3.1 节接受 `mapping_only` 注释层（门禁已支持，无需再改）；
4. 按第 4.1 节完成 `hasResultValue` 迁移并补充观测谓词一致性用例；
5. 对齐第 4.2 节度量分母新口径；
6. 以上完成后以 `PRISM_DIST_DIR` 或仓库同级目录指向 rc4 发行包重跑门禁，原子再生 `profile.lock`。
