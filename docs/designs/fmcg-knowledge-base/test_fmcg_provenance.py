#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_fmcg_provenance.py
谋圣·售点洞察 受管 FMCG 知识库溯源合规测试 - 严格断言 100% 实体均有权威出处
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import pytest  # noqa: E402
import yaml  # noqa: E402

from fmcg_knowledge_engine import (  # noqa: E402
    FMCGKnowledgeBase,
    CHANNEL_TAXONOMY,
    METRICS_PROVENANCE,
    BRAND_REGISTRY,
    COGNITIVE_CONCEPTS,
    KNOWLEDGE_BASE,
    REQUIRED_ENTITY_FIELDS,
    REQUIRED_PROVENANCE_FIELDS,
    ALLOWED_CONFIDENCE_TIERS,
    FORBIDDEN_CONFIDENCE_TIERS,
)


# ------------------------------------------------------------------------------
# 1. 文件存在性
# ------------------------------------------------------------------------------
def test_all_provenance_files_exist():
    for p in [CHANNEL_TAXONOMY, METRICS_PROVENANCE, BRAND_REGISTRY,
              COGNITIVE_CONCEPTS, KNOWLEDGE_BASE]:
        assert p.exists(), f"Missing required provenance file: {p}"


# ------------------------------------------------------------------------------
# 2. 严苛 CI Gate: 启动即生效
# ------------------------------------------------------------------------------
def test_kb_ci_gate_pass():
    kb = FMCGKnowledgeBase()  # 构造失败即 CI 不通过
    assert kb is not None


# ------------------------------------------------------------------------------
# 3. 全量实体覆盖率 (Channels + Metrics + Brands + Cognitive Concepts)
# ------------------------------------------------------------------------------
def _load_yaml(p):
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def test_every_entity_has_provenance_field():
    """每一个实体都必须带 provenance + confidence_tier。"""
    cnt_total, cnt_missing = 0, 0
    files = {
        "channel": (CHANNEL_TAXONOMY, "channels", True),
        "metric": (METRICS_PROVENANCE, "metrics", False),
        "brand": (BRAND_REGISTRY, "brands", False),
        "concept": (COGNITIVE_CONCEPTS, "concepts", False),
    }
    for cat, (path, root, recurse) in files.items():
        d = _load_yaml(path)
        items = d.get(root, [])
        for it in items:
            cnt_total += 1
            for f in REQUIRED_ENTITY_FIELDS:
                assert f in it, f"[{cat}] missing entity field: {f}"
            prov = it.get("provenance") or {}
            for f in REQUIRED_PROVENANCE_FIELDS:
                assert f in prov, f"[{cat}] missing provenance field: {f}"
            if recurse and "sub_channels" in it:
                for sub in it["sub_channels"]:
                    cnt_total += 1
                    for f in REQUIRED_ENTITY_FIELDS:
                        assert f in sub, f"[{cat}.sub] missing entity field: {f}"
                    prov = sub.get("provenance") or {}
                    for f in REQUIRED_PROVENANCE_FIELDS:
                        assert f in prov, f"[{cat}.sub] missing provenance field: {f}"
    assert cnt_total > 50, f"实体数量异常低: {cnt_total}"
    assert cnt_missing == 0


def test_no_forbidden_confidence_tiers():
    """禁止使用 UNVERIFIED / LLM_GENERATED_NO_SOURCE 等不可信等级。"""
    files = [CHANNEL_TAXONOMY, METRICS_PROVENANCE, BRAND_REGISTRY, COGNITIVE_CONCEPTS]
    bad = []
    for p in files:
        d = _load_yaml(p)
        for cat in ("channels", "metrics", "brands", "concepts"):
            for it in d.get(cat, []) or []:
                t = (it.get("provenance") or {}).get("confidence_tier")
                if t in FORBIDDEN_CONFIDENCE_TIERS:
                    bad.append((cat, it.get("uri"), t))
                if t not in ALLOWED_CONFIDENCE_TIERS and t is not None:
                    bad.append((cat, it.get("uri"), f"INVALID_TIER:{t}"))
                for sub in it.get("sub_channels") or []:
                    t2 = (sub.get("provenance") or {}).get("confidence_tier")
                    if t2 in FORBIDDEN_CONFIDENCE_TIERS:
                        bad.append((cat, sub.get("uri"), t2))
    assert not bad, f"FORBIDDEN_TIERS detected: {bad}"


# ------------------------------------------------------------------------------
# 4. URI 命名空间一致性
# ------------------------------------------------------------------------------
def test_uri_namespace_uniqueness():
    """每个 URI 必须全局唯一, 且命名空间遵循 prism:// 约定。"""
    seen = {}
    dups = []
    for p, root in [
        (CHANNEL_TAXONOMY, "channels"),
        (METRICS_PROVENANCE, "metrics"),
        (BRAND_REGISTRY, "brands"),
        (COGNITIVE_CONCEPTS, "concepts"),
    ]:
        d = _load_yaml(p)
        for it in d.get(root, []) or []:
            u = it.get("uri")
            assert u and u.startswith("prism:"), f"URI missing namespace: {u}"
            if u in seen:
                dups.append((u, seen[u], root))
            seen[u] = root
            for sub in it.get("sub_channels") or []:
                us = sub.get("uri")
                assert us and us.startswith("prism:"), f"URI missing namespace: {us}"
                if us in seen:
                    dups.append((us, seen[us], root + ".sub"))
                seen[us] = root
    assert not dups, f"URI duplicate: {dups}"


# ------------------------------------------------------------------------------
# 5. 反向归因 API 测试
# ------------------------------------------------------------------------------
def test_diagnose_text_returns_provenance():
    """业务口语诊断必须返回带 provenance 的命中记录。"""
    kb = FMCGKnowledgeBase()
    hits = kb.diagnose_text("查一下夫妻老婆店的清单")
    assert len(hits) >= 1
    assert any(h.get("provenance") for h in hits), "诊断结果缺失 provenance"
    for h in hits:
        assert h.get("uri") and h.get("uri").startswith("prism:"), f"诊断命中缺少 URI: {h}"
        assert h.get("provenance", {}).get("confidence_tier") in ALLOWED_CONFIDENCE_TIERS, \
            f"诊断命中置信度不合规: {h}"


def test_diagnose_text_brand_recognition():
    kb = FMCGKnowledgeBase()
    hits = kb.diagnose_text("看一下瑞幸和洋快餐")
    uris = {h.get("uri") for h in hits}
    assert "prism:brand/LuckinCoffee" in uris
    assert "prism:concept/WesternFastFoodChinese" in uris


# ------------------------------------------------------------------------------
# 6. 与 field_registry.json v1.2.0 链接一致性
# ------------------------------------------------------------------------------
def test_field_registry_linkage():
    """field_registry.json 中的 domain_entities 全部带 uri + provenance, 引用 FMCG KB。"""
    import json
    fr_path = SCRIPT_DIR.parent / "references" / "field_registry.json"
    fr = json.loads(fr_path.read_text(encoding="utf-8"))
    assert fr.get("semantic_layer_version") == "1.2.0", \
        f"field_registry.json 语义层版本必须为 1.2.0, 实际 {fr.get('semantic_layer_version')}"
    entities = fr.get("domain_entities") or {}
    assert len(entities) >= 11, f"domain_entities 应 >= 11, 实际 {len(entities)}"
    missing_uri = [k for k, v in entities.items() if not v.get("uri")]
    missing_prov = [k for k, v in entities.items() if not v.get("provenance")]
    assert not missing_uri, f"实体缺少 uri: {missing_uri}"
    assert not missing_prov, f"实体缺少 provenance: {missing_prov}"


# ------------------------------------------------------------------------------
# 7. agent_core.Provenance 注入 (smoke)
# ------------------------------------------------------------------------------
def test_agent_core_diagnose_text_smoke():
    """agent_core.FieldRegistry.diagnose_text() 集成测试, 必须在 fmcg_knowledge_engine 之外仍可返回 uri。"""
    import agent_core as A
    reg = A.FieldRegistry.load(SCRIPT_DIR.parent / "references" / "field_registry.json")
    hits = reg.diagnose_text("查一下美宜佳")
    assert len(hits) >= 1
    assert any(h.get("uri") == "prism:brand/Meiyijia" for h in hits), \
        "agent_core 应能返回美宜佳 brand uri"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))