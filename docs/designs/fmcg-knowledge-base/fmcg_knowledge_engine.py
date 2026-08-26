#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fmcg_knowledge_engine.py
谋圣·售点洞察 (mousheng-outlet-insight) - 受管 FMCG 领域知识库引擎

职责:
  1. 加载并合并 `skill/references/` 下 4 个权威溯源 YAML 资产
  2. 实施 W3C PROV-O + ISO 11179 合规的严苛 CI Gate (100% 实体必须有 source)
  3. 编译输出语义层注入块 (Semantic Overlay), 供 agent_core.py 与 insight.py 调用
  4. 暴露 diagnose_text(q) 反向归因: 给定业务口语问句, 输出所触发的概念与其溯源

执行: python skill/scripts/fmcg_knowledge_engine.py --ci
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # PyYAML
except ImportError:
    print("[FATAL] PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

# ==============================================================================
# 常量与路径
# ==============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REFS_DIR = SKILL_DIR / "references"

CHANNEL_TAXONOMY = REFS_DIR / "fmcg_channel_taxonomy.yaml"
METRICS_PROVENANCE = REFS_DIR / "fmcg_metrics_provenance.yaml"
BRAND_REGISTRY = REFS_DIR / "fmcg_brand_registry.yaml"
COGNITIVE_CONCEPTS = REFS_DIR / "cognitive_concepts_provenance.yaml"
KNOWLEDGE_BASE = REFS_DIR / "fmcg_knowledge_base.yaml"

# ISO 11179 + W3C PROV-O 强制要求的实体属性
REQUIRED_ENTITY_FIELDS = {"uri", "prefLabel", "definition", "provenance"}
REQUIRED_PROVENANCE_FIELDS = {"authority_ref", "confidence_tier"}
ALLOWED_CONFIDENCE_TIERS = {
    "STATUTORY", "INDUSTRY_STANDARD", "ENTERPRISE_PRACTICE",
    "ACADEMIC_AUTHORITY", "OPEN_KNOWLEDGE", "EMPIRICAL",
}
FORBIDDEN_CONFIDENCE_TIERS = {"UNVERIFIED", "LLM_GENERATED_NO_SOURCE"}


# ==============================================================================
# 加载与编译
# ==============================================================================
def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"[FMCG-KB] Missing required file: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class FMCGKnowledgeBase:
    """受管 FMCG 领域知识库(只读 + 编译期校验)。

    使用方式:
        kb = FMCGKnowledgeBase()             # 启动即强制 CI Gate
        kb.export_semantic_overlay()         # 导出供 agent_core.py / insight.py 使用的 JSON 块
        kb.diagnose_text("查一下夫妻老婆店的清单")  # 反向归因业务口语
    """

    def __init__(self) -> None:
        self.base_meta = _load_yaml(KNOWLEDGE_BASE)
        self.channels_raw = _load_yaml(CHANNEL_TAXONOMY).get("channels", [])
        self.metrics_raw = _load_yaml(METRICS_PROVENANCE).get("metrics", [])
        self.brands_raw = _load_yaml(BRAND_REGISTRY).get("brands", [])
        self.concepts_raw = _load_yaml(COGNITIVE_CONCEPTS).get("concepts", [])
        self._ci_gate()  # 启动即强制执行严苛 CI Gate

    # ------------------------------------------------------------------ CI
    def _ci_gate(self) -> None:
        """W3C PROV-O + ISO 11179 强制门禁: 每个实体必须有 source & authority。"""
        errors: List[str] = []

        def _check_entity(ent: Dict[str, Any], category: str, idx: int) -> None:
            tag = f"[{category}#{idx}]"
            missing = REQUIRED_ENTITY_FIELDS - ent.keys()
            if missing:
                errors.append(f"{tag} missing required fields: {missing}")
                return
            prov = ent.get("provenance") or {}
            pmiss = REQUIRED_PROVENANCE_FIELDS - prov.keys()
            if pmiss:
                errors.append(f"{tag} provenance missing: {pmiss}")
            tier = prov.get("confidence_tier")
            if tier in FORBIDDEN_CONFIDENCE_TIERS:
                errors.append(f"{tag} forbidden confidence tier: {tier}")
            if tier not in ALLOWED_CONFIDENCE_TIERS and tier is not None:
                errors.append(f"{tag} invalid confidence tier: {tier}")

        for i, c in enumerate(self.channels_raw):
            _check_entity(c, "Channel", i)
            for j, sub in enumerate(c.get("sub_channels") or []):
                _check_entity(sub, f"Channel.Sub({c.get('code')})", j)
        for i, m in enumerate(self.metrics_raw):
            _check_entity(m, "Metric", i)
        for i, b in enumerate(self.brands_raw):
            _check_entity(b, "Brand", i)
        for i, k in enumerate(self.concepts_raw):
            _check_entity(k, "Concept", i)

        if errors:
            print("[FMCG-KB][CI-GATE] 严苛溯源校验失败! 错误如下:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            raise RuntimeError(
                f"FMCG Knowledge Base failed PROV-O/ISO-11179 CI Gate: {len(errors)} errors"
            )

        # 全局 URI 唯一性
        seen_uris: Dict[str, str] = {}
        dups: List[str] = []
        for category, items in (
            ("Channel", self.channels_raw),
            ("Metric", self.metrics_raw),
            ("Brand", self.brands_raw),
            ("Concept", self.concepts_raw),
        ):
            for it in items:
                u = it.get("uri")
                if u in seen_uris:
                    dups.append(f"{u} duplicated in {category} & {seen_uris[u]}")
                seen_uris[u] = category
            for c in [it for it in items if it.get("sub_channels")]:
                for sub in c["sub_channels"]:
                    u = sub.get("uri")
                    if u in seen_uris:
                        dups.append(f"{u} duplicated in sub_channel & {seen_uris[u]}")
                    seen_uris[u] = category
        if dups:
            raise RuntimeError(
                f"[FMCG-KB][CI-GATE] Duplicate URIs: {len(dups)} -> {dups[:3]}"
            )

        total = (
            sum(1 for _ in self.channels_raw)
            + sum(len(c.get("sub_channels") or []) for _ in self.channels_raw)
            + sum(1 for _ in self.metrics_raw)
            + sum(1 for _ in self.brands_raw)
            + sum(1 for _ in self.concepts_raw)
        )
        print(f"[FMCG-KB][CI-GATE] 严苛溯源校验通过: {total} entities, 0 missing source.")

    # ------------------------------------------------------------------ 导出
    def export_semantic_overlay(self) -> Dict[str, Any]:
        """导出供下游 (agent_core.py / insight.py / field_registry.json) 消费的语义注入块。"""
        channels = []
        for c in self.channels_raw:
            entry = {
                "uri": c.get("uri"),
                "code": c.get("code"),
                "prefLabel": c.get("prefLabel"),
                "altLabels": c.get("altLabels", []),
                "definition": c.get("definition"),
                "provenance": c.get("provenance"),
                "sub_channels": [
                    {
                        "uri": s.get("uri"),
                        "code": s.get("code"),
                        "prefLabel": s.get("prefLabel"),
                        "altLabels": s.get("altLabels", []),
                        "definition": s.get("definition"),
                        "physical_mapping": s.get("physical_mapping"),
                        "provenance": s.get("provenance"),
                    }
                    for s in (c.get("sub_channels") or [])
                ],
            }
            channels.append(entry)
        return {
            "version": self.base_meta.get("version"),
            "schema": self.base_meta.get("schema"),
            "channels": channels,
            "metrics": self.metrics_raw,
            "brands": self.brands_raw,
            "concepts": self.concepts_raw,
        }

    # ------------------------------------------------------------------ 反向归因
    def diagnose_text(self, query: str) -> List[Dict[str, Any]]:
        """给定业务口语问句, 返回触发的认知概念 + 品牌 + 业态及其权威溯源。

        用于在 AnalysisIntent 层向用户展示 "我的理解来自哪里"。
        """
        hits: List[Dict[str, Any]] = []
        q = query or ""

        for c in self.concepts_raw:
            labels = [c.get("prefLabel", "")] + (c.get("altLabels") or [])
            if any(lab and lab in q for lab in labels):
                hits.append(
                    {
                        "type": "CognitiveConcept",
                        "uri": c.get("uri"),
                        "matched_label": next(
                            (lab for lab in labels if lab and lab in q), c.get("prefLabel")
                        ),
                        "mapped_rule": c.get("mapped_logical_rule"),
                        "definition": c.get("definition"),
                        "provenance": c.get("provenance"),
                    }
                )

        for b in self.brands_raw:
            labels = [b.get("prefLabel", "")] + (b.get("altLabels") or [])
            if any(lab and lab in q for lab in labels):
                hits.append(
                    {
                        "type": "Brand",
                        "uri": b.get("uri"),
                        "matched_label": next(
                            (lab for lab in labels if lab and lab in q), b.get("prefLabel")
                        ),
                        "category": b.get("category"),
                        "parent_company": b.get("parent_company"),
                        "provenance": b.get("provenance"),
                    }
                )

        for c in self.channels_raw:
            labels = [c.get("prefLabel", "")] + (c.get("altLabels") or [])
            if any(lab and lab in q for lab in labels):
                hits.append(
                    {
                        "type": "Channel",
                        "uri": c.get("uri"),
                        "matched_label": next(
                            (lab for lab in labels if lab and lab in q), c.get("prefLabel")
                        ),
                        "provenance": c.get("provenance"),
                    }
                )
            for s in c.get("sub_channels") or []:
                labels = [s.get("prefLabel", "")] + (s.get("altLabels") or [])
                if any(lab and lab in q for lab in labels):
                    hits.append(
                        {
                            "type": "Channel.SubChannel",
                            "uri": s.get("uri"),
                            "matched_label": next(
                                (lab for lab in labels if lab and lab in q),
                                s.get("prefLabel"),
                            ),
                            "physical_mapping": s.get("physical_mapping"),
                            "provenance": s.get("provenance"),
                        }
                    )
        return hits


# ==============================================================================
# CLI
# ==============================================================================
def _cmd_ci() -> int:
    kb = FMCGKnowledgeBase()
    overlay = kb.export_semantic_overlay()
    print(
        f"[FMCG-KB] channels={len(overlay['channels'])} metrics={len(overlay['metrics'])} "
        f"brands={len(overlay['brands'])} concepts={len(overlay['concepts'])}"
    )
    return 0


def _cmd_diagnose(query: str) -> int:
    kb = FMCGKnowledgeBase()
    hits = kb.diagnose_text(query)
    print(json.dumps({"query": query, "hits": hits}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="谋圣受管 FMCG 知识库引擎")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ci", help="运行严苛溯源 CI Gate")
    p_diag = sub.add_parser("diagnose", help="反向归因业务口语到受管概念")
    p_diag.add_argument("--query", required=True, help="业务口语问句")
    args = parser.parse_args()

    if args.cmd == "ci":
        return _cmd_ci()
    if args.cmd == "diagnose":
        return _cmd_diagnose(args.query)
    return 1


if __name__ == "__main__":
    sys.exit(main())