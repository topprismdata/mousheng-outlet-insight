# -*- coding: utf-8 -*-
"""
dataframe_adapter.py
====================
外围数据适配器 (v1.2 增强版)：
将底层执行引擎产出的 pandas DataFrame、查询 DSL 与审计字典，
转换为包含丰富证据语义 (Evidence Semantics v1.2) 的标准三元输入契约 (InsightRequest)：
1. AnalysisIntent (分析意图字典，含 target_quota 与 priority_funnel)
2. EvidenceSet (包含显式 dimensions, measures, grain, time_window, quota_evaluation 与 evidence_provenance)
3. QualityContext (质量上下文字典)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
import numpy as np
import pandas as pd


# 常见度量语义元数据字典（单位、聚合方式、本体 URI）
METRIC_SEMANTIC_REGISTRY = {
    "门店数": {"uri": "prism:metric/OutletCount", "unit": "家", "aggregation": "count", "data_type": "integer"},
    "客户数": {"uri": "prism:metric/OutletCount", "unit": "家", "aggregation": "count", "data_type": "integer"},
    "cnt": {"uri": "prism:metric/OutletCount", "unit": "家", "aggregation": "count", "data_type": "integer"},
    "餐饮基数": {"uri": "prism:metric/OutletCount", "unit": "家", "aggregation": "count", "data_type": "integer"},
    "达标率": {"uri": "prism:metric/RuleComplianceRate", "unit": "分", "aggregation": "score", "data_type": "float"},
    "周末均值客流(人)": {"uri": "prism:metric/WeekendTraffic", "unit": "人/天", "aggregation": "mean", "data_type": "float"},
    "平日均值客流(人)": {"uri": "prism:metric/WeekdayTraffic", "unit": "人/天", "aggregation": "mean", "data_type": "float"},
    "平均客流": {"uri": "prism:metric/WeekendTraffic", "unit": "人/天", "aggregation": "mean", "data_type": "float"},
    "大众点评餐饮点评评分星级": {"uri": "prism:metric/DianpingRating", "unit": "星", "aggregation": "mean", "data_type": "float"},
    "携程-综合评分": {"uri": "prism:metric/CtripRating", "unit": "分", "aggregation": "mean", "data_type": "float"},
    "携程-房间数": {"uri": "prism:metric/CtripRoomCount", "unit": "间", "aggregation": "sum", "data_type": "integer"},
    "月均收入潜力预测(无协议无冰柜)": {"uri": "prism:estimate/RevenuePotentialNoAgreementNoCooler", "unit": "元/月", "aggregation": "derived", "data_type": "currency"},
    "营业面积": {"uri": "prism:estimate/OperatingAreaSquareMeters", "unit": "m²", "aggregation": "mean", "data_type": "float"}
}

# 常见维度概念 URI 映射
DIMENSION_CONCEPT_MAP = {
    "连锁品牌名称": {"uri": "prism:outlet/Brand", "type": "category", "grain": "brand"},
    "渠道组": {"uri": "prism:outlet/ChannelType", "type": "category", "grain": "channel_type"},
    "细分渠道": {"uri": "prism:outlet/ChannelType", "type": "category", "grain": "channel_type"},
    "规划大区": {"uri": "prism:outlet/Territory", "type": "spatial", "grain": "territory"},
    "规划办事处": {"uri": "prism:outlet/Territory", "type": "spatial", "grain": "territory"},
    "省份": {"uri": "prism:outlet/AdministrativeRegion", "type": "spatial", "grain": "administrative_region"},
    "城市": {"uri": "prism:outlet/AdministrativeRegion", "type": "spatial", "grain": "administrative_region"},
    "区县": {"uri": "prism:outlet/AdministrativeRegion", "type": "spatial", "grain": "administrative_region"},
    "经营场所名称": {"uri": "prism:outlet/CommercialSite", "type": "spatial", "grain": "commercial_site"},
    "经营场所": {"uri": "prism:outlet/CommercialSite", "type": "category", "grain": "commercial_site"},
    "腾讯售点名称": {"uri": "prism:outlet/Outlet", "type": "identifier", "grain": "outlet"},
    "腾讯售点id": {"uri": "prism:outlet/Outlet", "type": "identifier", "grain": "outlet"},
}


def extract_insight_request(dsl: dict, result_df: pd.DataFrame | None,
                            audit_report: dict | None = None,
                            request_id: str | None = None,
                            dataset_id: str = "outlet-data-profile",
                            data_version: str = "2026.08",
                            ontology_profile_version: str = "0.1.0-rc2",
                            dataset_mapping_version: str = "2.1.0") -> dict[str, Any]:
    """将 (dsl, result_df, audit_report) 转换为符合 insight-request.schema.json v1.2 的请求对象。"""
    req_id = request_id or f"req-{uuid.uuid4().hex[:10]}"
    qtype = (dsl or {}).get("type", "A")
    raw_intent = (dsl or {}).get("analysis_intent") or {}

    # 1. 组装 AnalysisIntent
    q_text = raw_intent.get("question") or (dsl.get("nl") if isinstance(dsl, dict) else "") or "售点数据分析请求"
    target_q = raw_intent.get("target_quota") or dsl.get("target_quota") or dsl.get("limit")
    analysis_intent = {
        "question": q_text,
        "intent": raw_intent.get("intent") or ("detail_list" if qtype == "A" else ("distribution_comparison" if qtype == "B" else "rule_ranking")),
        "target_entity": raw_intent.get("target_entity", "prism:outlet/Outlet"),
        "scope": raw_intent.get("scope", {}),
        "entities": raw_intent.get("entities", []),
        "metrics": raw_intent.get("metrics", []),
        "rules": raw_intent.get("rules", []),
        "target_quota": target_q,
        "priority_funnel": raw_intent.get("priority_funnel") or dsl.get("priority_funnel", []),
        "ambiguities": raw_intent.get("ambiguities", []),
        "requested_outputs": raw_intent.get("requested_outputs", ["insight", "table"])
    }

    # 2. 组装 EvidenceProvenance
    evidence_provenance = {
        "dataset_id": dataset_id,
        "data_version": data_version,
        "ontology_profile_version": ontology_profile_version,
        "dataset_mapping_version": dataset_mapping_version,
        "engine_run_id": f"engine-{uuid.uuid4().hex[:8]}",
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

    # 3. 提取显式维度与度量语义 (v1.2 增强)
    dimensions = []
    measures = []
    grain = "unspecified"

    if result_df is None or len(result_df) == 0:
        sample_size = 0
        effective_size = 0
        records = []
        drilldown_keys = []
        primary_dim = (dsl.get("group_by") or [None])[0] if isinstance(dsl, dict) else None
        metric_cols = []
    else:
        sample_size = len(result_df)
        effective_size = sample_size
        # 仅对前 100 行记录进行 JSON 序列化（用于 table_preview），避免全表数十万行开销，保证秒级响应
        preview_df = result_df.head(100)
        records = json.loads(preview_df.to_json(orient="records", force_ascii=False))
        
        # 确定主维度与度量列
        if qtype == "B":
            primary_dim = (dsl.get("group_by") or [None])[0]
        elif qtype == "C":
            primary_dim = next((c for c in ("经营场所名称", "经营场所", "候选") if c in result_df.columns), None)
        else:
            primary_dim = next((c for c in ("规划大区", "省份", "城市", "渠道组") if c in result_df.columns), None)

        metric_cols = [c for c in result_df.columns if c != primary_dim]

        # 构造 dimensions 列表
        for c in result_df.columns:
            dim_spec = DIMENSION_CONCEPT_MAP.get(c)
            if dim_spec:
                card = int(result_df[c].nunique())
                dimensions.append({
                    "name": c,
                    "uri": dim_spec["uri"],
                    "type": dim_spec["type"],
                    "cardinality": card
                })
                if c == primary_dim:
                    grain = dim_spec.get("grain", "unspecified")

        # 构造 measures 列表
        for c in metric_cols:
            spec = METRIC_SEMANTIC_REGISTRY.get(c, {
                "uri": f"prism:metric/{c}",
                "unit": "数值",
                "aggregation": "mean" if ("评分" in c or "客流" in c) else ("count" if ("数" in c or "量" in c) else "sum"),
                "data_type": "float"
            })
            measures.append({
                "name": c,
                "uri": spec["uri"],
                "unit": spec["unit"],
                "aggregation": spec["aggregation"],
                "data_type": spec.get("data_type", "float"),
                "description": f"源自列「{c}」的度量数据"
            })

        # 提取下钻键
        drilldown_keys = []
        if primary_dim and primary_dim in result_df.columns:
            top_vals = result_df[primary_dim].dropna().unique()[:5]
            drilldown_keys = [{primary_dim: str(v)} for v in top_vals]
        elif any(c in result_df.columns for c in ["腾讯售点id", "KO_ID", "客资编号"]):
            id_col = next(c for c in ("腾讯售点id", "KO_ID", "客资编号") if c in result_df.columns)
            drilldown_keys = [{id_col: str(v)} for v in result_df[id_col].head(5)]

    # 4. 提取 quota_evaluation
    quota_evaluation = None
    if audit_report and audit_report.get("quota_evaluation"):
        quota_evaluation = audit_report["quota_evaluation"]
    elif target_q:
        ps = sample_size
        ss = min(sample_size, target_q)
        quota_evaluation = {
            "target_quota": int(target_q),
            "pool_size": int(ps),
            "selected_size": int(ss),
            "fulfillment_rate": float(round(ss / target_q, 2)) if target_q else 1.0,
            "fulfillment_status": "fulfilled" if ss >= target_q else "gap_detected",
            "gap_count": int(max(0, target_q - ss)),
            "cutoff_thresholds": {}
        }

    # 5. 构造 EvidenceSet (v1.2 增强)
    evidence_set: dict[str, Any] = {
        "sample_size": sample_size,
        "effective_sample_size": effective_size,
        "primary_dimension": primary_dim,
        "metric_columns": metric_cols,
        "dimensions": dimensions,
        "measures": measures,
        "grain": grain,
        "time_window": {
            "start": "2026-08-01",
            "end": "2026-08-25",
            "period_type": "snapshot",
            "as_of_date": "2026-08-25"
        },
        "records": records,
        "drilldown_keys": drilldown_keys,
        "out_csv_path": None,
        "chart_spec_ref": None,
        "evidence_provenance": evidence_provenance
    }
    if quota_evaluation is not None:
        evidence_set["quota_evaluation"] = quota_evaluation

    # 6. 组装 QualityContext
    field_coverage = {}
    if audit_report and audit_report.get("field_missing"):
        field_coverage = {k: round((100 - v) / 100.0, 2) for k, v in audit_report["field_missing"].items()}
    elif result_df is not None and len(result_df) > 0:
        for c in result_df.columns:
            field_coverage[c] = round(float(result_df[c].notna().mean()), 2)

    completeness = round(float(np.mean(list(field_coverage.values()))), 2) if field_coverage else 1.0

    quality_context = {
        "completeness_score": completeness,
        "field_coverage": field_coverage,
        "applicable_dimensions": list(field_coverage.keys()),
        "quality_flags": audit_report.get("flags", []) if audit_report else [],
        "quality_notice": audit_report.get("recommendation") if audit_report else None
    }

    return {
        "request_id": req_id,
        "analysis_intent": analysis_intent,
        "evidence_set": evidence_set,
        "quality_context": quality_context
    }
