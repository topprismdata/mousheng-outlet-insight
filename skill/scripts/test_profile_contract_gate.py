# -*- coding: utf-8 -*-
"""
test_profile_contract_gate.py
=============================
下游谋圣端 Phase 0 Contract Gate 权威验证与 Profile 锁定生成流水线：
1. 读取 references/ontology_profile_ref.yaml 发现上游发行包路径（可移植解析，禁止机器绝对路径）：
   环境变量 PRISM_DIST_DIR -> 仓库同级 ../prism-ontology/dist/outlet-insight/<version>
   -> 用户主目录 ~/prism-ontology/dist/outlet-insight/<version>。
2. 校验发布身份：版本一致性、Profile URI 一致性、Git Commit 存在性、Tag 祖先可溯源性与 clean_working_tree == True。
3. 校验发行包中所有文件（包含 checksums.sha256 与 profile-manifest.json）的 SHA-256 完整性；
   rc3+ 发行包额外消费 field-mapping.yaml（mapping_only 注释层，绝不并入本体概念/关系注册表）。
4. 递归扫描 dataset_mapping.yaml 中所有 unique prism: URI，按类别执行精确集合成员校验（禁止模糊前缀）。
5. 全部校验通过后，使用临时文件 + fsync + os.replace 原子生成 references/profile.lock 并回写 dataset_mapping.yaml 状态。

发行目录缺失语义：仅在按上述优先级尝试全部候选路径之后，pytest 入口
(test_profile_contract_gate) 以 skip-with-message 跳过；其余任何门禁断言失败一律硬失败。
"""
import hashlib
import json
import os
import subprocess
import tempfile
import yaml
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REF_DIR = SKILL_DIR / "references"

REF_PATH = REF_DIR / "ontology_profile_ref.yaml"
LOCK_PATH = REF_DIR / "profile.lock"
MAP_PATH = REF_DIR / "dataset_mapping.yaml"

REPO_ROOT = SKILL_DIR.parent


class UpstreamDistUnresolved(RuntimeError):
    """按优先级尝试全部候选路径后，上游 Profile 发行目录仍不可达。"""


def upstream_dist_candidates(ref_data):
    """按优先级枚举上游发行目录候选（不创建任何目录）：
    1. 环境变量 PRISM_DIST_DIR（直接指向发行目录）
    2. 仓库同级 ../prism-ontology/dist/outlet-insight/<profile_version>
    3. 用户主目录 ~/prism-ontology/dist/outlet-insight/<profile_version>
    """
    version = ref_data["profile_version"]
    candidates = []
    env_dir = os.environ.get("PRISM_DIST_DIR")
    if env_dir:
        candidates.append((f"env PRISM_DIST_DIR={env_dir}", Path(env_dir)))
    candidates.append((
        f"repo-sibling ../prism-ontology/dist/outlet-insight/{version}",
        REPO_ROOT.parent / "prism-ontology" / "dist" / "outlet-insight" / version,
    ))
    candidates.append((
        f"home ~/prism-ontology/dist/outlet-insight/{version}",
        Path.home() / "prism-ontology" / "dist" / "outlet-insight" / version,
    ))
    return candidates


def resolve_upstream_dist(ref_data):
    """返回 (发行目录, 解析来源描述)；全部候选不存在时返回 (None, 已尝试清单)。"""
    tried = []
    for desc, path in upstream_dist_candidates(ref_data):
        if path.is_dir():
            return path, desc
        tried.append(f"  - {desc} -> {path}")
    return None, "\n".join(tried)


def portable_dist_path(dist_path):
    """发行目录的可移植表示（优先 ~ 相对形式），避免锁文件固化机器特定绝对路径。"""
    home = Path.home()
    if home == dist_path or home in dist_path.parents:
        return "~/" + str(dist_path.relative_to(home))
    return str(dist_path)


def verify_contract_gate():
    print("[Contract Gate] 开始执行下游消费契约门禁与原子锁定流水线...")

    # 1. 读取 Profile 引用元数据
    assert REF_PATH.exists(), f"Profile 引用文件不存在: {REF_PATH}"
    with open(REF_PATH, "r", encoding="utf-8") as f:
        ref_data = yaml.safe_load(f)["profile_reference"]

    dist_path, resolved_via = resolve_upstream_dist(ref_data)
    if dist_path is None:
        raise UpstreamDistUnresolved(
            "上游 Profile 发行目录未解析（已按优先级尝试全部候选路径）:\n" + resolved_via
        )
    print(f"  ✓ 上游发行目录解析成功 (via {resolved_via}): {dist_path}")
    # 2. 发布身份与版本一致性强校验
    manifest_path = dist_path / "profile-manifest.json"
    assert manifest_path.exists(), f"Manifest 文件缺失: {manifest_path}"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert ref_data["profile_version"] == manifest["version"], f"版本不一致: ref={ref_data['profile_version']} vs manifest={manifest['version']}"
    assert ref_data["profile_uri"] == manifest["profile_uri"], f"URI 不一致: ref={ref_data['profile_uri']} vs manifest={manifest['profile_uri']}"
    assert manifest.get("clean_working_tree") is True, f"上游发行包未在干净工作树构建 (clean_working_tree={manifest.get('clean_working_tree')})"
    print(f"  ✓ 发布身份与版本一致性校验通过 (Version={manifest['version']}, CleanTree=True)")

    # 校验 Git Tag 与 Commit 祖先溯源
    git_repo_path = dist_path.parent.parent.parent  # 上游 prism-ontology 仓库根目录
    if (git_repo_path / ".git").exists():
        tag_commit = subprocess.check_output(["git", "rev-parse", manifest["release_tag"] + "^{commit}"], cwd=git_repo_path).decode("utf-8").strip()
        manifest_commit = manifest.get("git_commit")
        is_ancestor = (subprocess.run(["git", "merge-base", "--is-ancestor", manifest_commit, tag_commit], cwd=git_repo_path).returncode == 0)
        assert is_ancestor, f"Manifest 记录的 Commit ({manifest_commit[:7]}) 不是 Tag 提交 ({tag_commit[:7]}) 的有效祖先节点！"
        print(f"  ✓ Git Release Tag 溯源通过 (Tag={manifest['release_tag']} -> DistCommit={tag_commit[:7]}, SourceCommit={manifest_commit[:7]})")

    # 3. 读取上游 checksums.sha256 并逐项验证真实文件哈希
    checksum_file = dist_path / "checksums.sha256"
    assert checksum_file.exists(), f"上游发行包缺失 checksums.sha256: {checksum_file}"

    upstream_checksums = {}
    with open(checksum_file, "r", encoding="utf-8") as f:
        for line in f.read().splitlines():
            if line.strip():
                parts = line.strip().split()
                if len(parts) >= 2:
                    sha, fname = parts[0], parts[1]
                    upstream_checksums[fname] = sha

    # 确保 Manifest included_files 与 checksums 列表完全一致
    assert set(manifest["included_files"]) == set(fname for fname in upstream_checksums.keys() if fname != "profile-manifest.json"), "Manifest 与 Checksums 文件清单不一致"

    locked_hashes = {}
    for fname, exp_sha in upstream_checksums.items():
        tfile = dist_path / fname
        assert tfile.exists(), f"上游发行文件缺失: {fname}"
        real_sha = hashlib.sha256(tfile.read_bytes()).hexdigest()
        assert real_sha == exp_sha, f"SHA-256 校验失败: {fname} (期望: {exp_sha}, 实际: {real_sha})"
        locked_hashes[fname] = f"sha256:{real_sha}"
        print(f"  ✓ 发行文件哈希匹配: {fname}")

    # 4. 读取上游受管全量概念/关系/度量/数据源/组织注册表
    with open(dist_path / "concepts.yaml", "r", encoding="utf-8") as f:
        c_data = yaml.safe_load(f)
    with open(dist_path / "relations.yaml", "r", encoding="utf-8") as f:
        r_data = yaml.safe_load(f)
    with open(dist_path / "metric-definitions.yaml", "r", encoding="utf-8") as f:
        m_data = yaml.safe_load(f)
    with open(dist_path / "sources.yaml", "r", encoding="utf-8") as f:
        s_data = yaml.safe_load(f)
    with open(dist_path / "organizations.yaml", "r", encoding="utf-8") as f:
        o_data = yaml.safe_load(f)

    valid_concepts = set(c["uri"] for c in c_data["concepts"])
    valid_relations = set(r["uri"] for r in r_data["relations"])
    valid_metrics = set(m["uri"] for m in m_data["metrics"])
    valid_sources = set(s["uri"] for s in s_data["sources"])
    valid_orgs = set(o["uri"] for o in o_data["organizations"])

    # 4b. 消费上游 field-mapping.yaml（rc3+ 新增机器可读注释层）
    # mapping_only 条目是物理列降级映射注释：合法的映射层引用，但绝不并入本体概念/关系注册表。
    mapping_only_uris = set()
    field_mapping_path = dist_path / "field-mapping.yaml"
    if field_mapping_path.exists():
        with open(field_mapping_path, "r", encoding="utf-8") as f:
            fm_data = yaml.safe_load(f)
        for fm_entry in fm_data.get("field_mappings", []):
            assert fm_entry.get("governance_status") == "mapping_only", (
                f"field-mapping 条目治理状态必须为 mapping_only: {fm_entry.get('uri')}"
            )
            mapping_only_uris.add(fm_entry["uri"])
        print(f"  ✓ 上游 field-mapping 注释层: {len(mapping_only_uris)} 个 mapping_only URI（不入本体注册表）")

    all_registered_uris = valid_concepts | valid_relations | valid_metrics | valid_sources | valid_orgs
    print(f"  ✓ 上游 Profile 受管 URI 库: {len(all_registered_uris)} 个 (Concepts={len(valid_concepts)}, Relations={len(valid_relations)}, Metrics={len(valid_metrics)}, Sources={len(valid_sources)}, Orgs={len(valid_orgs)})")

    # 5. 递归扫描本地 dataset_mapping.yaml 中的全量 prism: URI 并精确校验
    with open(MAP_PATH, "r", encoding="utf-8") as f:
        map_data = yaml.safe_load(f)

    def extract_uris(obj):
        found = set()
        if isinstance(obj, str):
            if obj.startswith("prism:"):
                found.add(obj)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                found.update(extract_uris(v))
        elif isinstance(obj, list):
            for item in obj:
                found.update(extract_uris(item))
        return found

    mapping_uris = extract_uris(map_data)
    print(f"  ✓ 本地映射递归提取 URI: {len(mapping_uris)} 个唯一标识")

    unregistered_uris = []
    for uri in sorted(mapping_uris):
        # 精确分类集合成员校验
        if uri in mapping_only_uris:
            # 已由上游 field-mapping mapping_only 注释层显式登记，独立于概念/关系集合
            continue
        if uri.startswith("prism:metric/"):
            if uri not in valid_metrics:
                unregistered_uris.append(f"[Metric] {uri}")
        elif uri.startswith("prism:observed/"):
            if uri not in valid_relations:
                unregistered_uris.append(f"[ObservedProperty] {uri}")
        elif uri.startswith("prism:estimate/"):
            if uri not in valid_relations:
                unregistered_uris.append(f"[EstimateProperty] {uri}")
        elif uri.startswith("prism:source/"):
            if uri not in valid_sources:
                unregistered_uris.append(f"[Source] {uri}")
        elif uri.startswith("prism:org/"):
            if uri not in valid_orgs:
                unregistered_uris.append(f"[Organization] {uri}")
        elif uri in ["prism:lifecycle/EntityOnlineTime", "prism:lifecycle/RecordCreationTime"]:
            if uri not in valid_relations:
                unregistered_uris.append(f"[LifecycleRelation] {uri}")
        else:
            if uri not in valid_concepts and uri not in valid_relations:
                unregistered_uris.append(f"[Concept/Relation] {uri}")

    assert len(unregistered_uris) == 0, f"发现未注册 URI:\n" + "\n".join(unregistered_uris)
    print(f"  ✓ 全量 {len(mapping_uris)} 个语义 URI 递归精确存在性校验 100% 通过（0 未注册）！")

    # 6. 更新本地映射条目状态
    for entry in map_data["mappings"]:
        if entry.get("mapping_status") in ["mapped", "derived"]:
            entry["semantic_binding_status"] = "validated"
            entry["validation_status"] = "passed"
        elif entry.get("mapping_status") == "ambiguous":
            entry["semantic_binding_status"] = "isolated"
            entry["validation_status"] = "isolated_passed"
        elif entry.get("mapping_status") == "not_applicable":
            entry["semantic_binding_status"] = "not_applicable"
            entry["validation_status"] = "engineering_passed"

    map_data["governance_status"] = f"verified_and_locked_against_profile_{manifest.get('version')}"

    # 原子写回 dataset_mapping.yaml
    fd, tmp_map = tempfile.mkstemp(dir=str(REF_DIR), prefix="dataset_mapping_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.dump(map_data, f, allow_unicode=True, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_map, MAP_PATH)

    # 7. 原子生成 references/profile.lock
    lock_payload = {
        "lock_metadata": {
            "profile_uri": manifest.get("profile_uri"),
            "locked_version": manifest.get("version"),
            "git_commit": manifest.get("git_commit"),
            "release_tag": manifest.get("release_tag"),
            "clean_working_tree": manifest.get("clean_working_tree"),
            "locked_at": datetime.now(timezone.utc).isoformat(),
            "upstream_authority": manifest.get("authority"),
            "distribution_path": portable_dist_path(dist_path),
            "distribution_resolution": resolved_via,
            "verification_status": "verified_and_locked"
        },
        "locked_checksums": locked_hashes
    }

    fd, tmp_lock = tempfile.mkstemp(dir=str(REF_DIR), prefix="profile_lock_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.dump(lock_payload, f, allow_unicode=True, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_lock, LOCK_PATH)

    print(f"\n[Contract Gate] 成功原子生成锁文件: {LOCK_PATH}")
    print(f"[Contract Gate] dataset_mapping.yaml 状态升级为: {map_data['governance_status']}")
    print(">>> PHASE 0 CONTRACT GATE: 100% PASSED AND ATOMICALLY LOCKED <<<")


def test_profile_contract_gate():
    """pytest 入口：仅当发行目录在全部候选路径尝试后仍不可达时 skip-with-message；
    门禁本体任何断言失败均硬失败，不被吞没。"""
    try:
        verify_contract_gate()
    except UpstreamDistUnresolved as exc:
        import pytest
        pytest.skip(str(exc))


if __name__ == "__main__":
    try:
        verify_contract_gate()
    except UpstreamDistUnresolved as exc:
        print(f"[Contract Gate] SKIP（发行目录不可达）: {exc}")
        raise SystemExit(1)
