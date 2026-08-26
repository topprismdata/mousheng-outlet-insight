# -*- coding: utf-8 -*-
"""insight.py - Insight Component v1.2 (Standard 11-field InsightPackage contract)."""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker

INSIGHT_COMPONENT_VERSION = "1.3.0-rc3"

SKILL_DIR = Path(__file__).resolve().parent.parent
REQ_SCHEMA_PATH = SKILL_DIR / "references" / "insight-request.schema.json"
PKG_SCHEMA_PATH = SKILL_DIR / "references" / "insight-package.schema.json"

FORMAT_CHECKER = FormatChecker()

@FORMAT_CHECKER.checks("date-time")
def _check_datetime_format(val: Any) -> bool:
    if not isinstance(val, str):
        return True
    try:
        datetime.fromisoformat(val.replace("Z", "+00:00"))
        return True
    except Exception:
        return False

with open(REQ_SCHEMA_PATH, "r", encoding="utf-8") as _f:
    _REQ_SCHEMA = json.load(_f)
with open(PKG_SCHEMA_PATH, "r", encoding="utf-8") as _f:
    _PKG_SCHEMA = json.load(_f)

REQ_VALIDATOR = Draft202012Validator(_REQ_SCHEMA, format_checker=FORMAT_CHECKER)
PKG_VALIDATOR = Draft202012Validator(_PKG_SCHEMA, format_checker=FORMAT_CHECKER)


class InsightContractError(ValueError):
    pass


def validate_insight_request(request: Any) -> None:
    """强制校验输入 InsightRequest：Schema 校验 + 语义约束校验。"""
    if not isinstance(request, dict):
        raise InsightContractError(f"InsightRequest 必须为字典类型，实测: {type(request).__name__}")
    
    errs = list(REQ_VALIDATOR.iter_errors(request))
    if errs:
        msgs = [f"[{'.'.join(str(p) for p in e.path) or 'root'}]: {e.message}" for e in errs]
        raise InsightContractError(f"InsightRequest 违反输入 Schema 契约 ({len(errs)} 处错误):\n" + "\n".join(msgs))

    # 语义约束：有效样本量不得大于总样本量
    ev = request["evidence_set"]
    if ev["effective_sample_size"] > ev["sample_size"]:
        raise InsightContractError(
            f"语义约束违规: effective_sample_size ({ev['effective_sample_size']}) 不能大于 sample_size ({ev['sample_size']})"
        )


def validate_insight_package(package: Any) -> None:
    """强制校验生成的 InsightPackage：Schema 强校验。"""
    errs = list(PKG_VALIDATOR.iter_errors(package))
    if errs:
        msgs = [f"[{'.'.join(str(p) for p in e.path) or 'root'}]: {e.message}" for e in errs]
        raise InsightContractError(f"生成的 InsightPackage 违反输出 Schema 契约 ({len(errs)} 处错误):\n" + "\n".join(msgs))


# =============================================================================
# 1. 确定性统计与多维分析算子库 (Pure Math & Statistical Operators)
# =============================================================================

def compute_concentration(counts: list[int | float]) -> dict[str, Any]:
    """计算产业经济学标准的市场集中度指标：HHI, CR4, CR8, Gini (纯数学实现)。"""
    valid = []
    for x in counts:
        if x is not None:
            try:
                fx = float(x)
                if not math.isnan(fx) and fx > 0:
                    valid.append(fx)
            except (ValueError, TypeError):
                pass

    if not valid:
        return {"hhi": 0.0, "cr4": 0.0, "cr8": 0.0, "gini": 0.0, "category": "unconcentrated"}

    total = sum(valid)
    sorted_v = sorted(valid, reverse=True)
    shares = [x / total for x in sorted_v]

    # HHI = Sum((s_i * 100)^2) (0 ~ 10000)
    hhi = round(sum((s * 100) ** 2 for s in shares), 1)

    # CR4 & CR8
    cr4 = round(sum(shares[:4]), 3)
    cr8 = round(sum(shares[:8]), 3)

    # Gini Coefficient
    n = len(sorted_v)
    if n <= 1:
        gini = 0.0
    else:
        asc = sorted(valid)
        n_sum = sum(asc)
        if n_sum == 0:
            gini = 0.0
        else:
            diff_sum = sum((2 * (i + 1) - n - 1) * y for i, y in enumerate(asc))
            gini = round(float(diff_sum / (n * n_sum)), 3)

    # 集中度等级分类
    if hhi >= 2500 or cr4 >= 0.65:
        category = "high_concentration"
    elif hhi >= 1500 or cr4 >= 0.40:
        category = "moderate_concentration"
    else:
        category = "unconcentrated"

    return {
        "hhi": hhi,
        "cr4": cr4,
        "cr8": cr8,
        "gini": max(0.0, min(1.0, gini)),
        "category": category,
    }


def detect_outliers_iqr_from_records(records: list[dict], val_col: str, id_col: str, name_col: str) -> list[dict]:
    """基于 Tukey IQR 箱线从纯 JSON 记录中检测离群点。"""
    vals = []
    for r in records:
        v = r.get(val_col)
        if v is not None:
            try:
                vals.append(float(v))
            except (ValueError, TypeError):
                pass

    if len(vals) < 4:
        return []

    sorted_vals = sorted(vals)
    n = len(sorted_vals)
    q1 = sorted_vals[int(n * 0.25)]
    q3 = sorted_vals[int(n * 0.75)]
    iqr = q3 - q1
    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr

    outliers = []
    for r in records:
        v = r.get(val_col)
        if v is not None:
            try:
                fv = float(v)
                if fv < lower_fence or fv > upper_fence:
                    outliers.append({
                        "entity_id": str(r.get(id_col, "")),
                        "entity_name": str(r.get(name_col, "")),
                        "metric": val_col,
                        "value": round(fv, 2),
                        "baseline_range": f"[{round(lower_fence, 1)}, {round(upper_fence, 1)}]",
                        "anomaly_type": "statistical_outlier"
                    })
            except (ValueError, TypeError):
                pass
    return outliers[:5]


def assess_confidence(sample_size: int, effective_size: int, field_cov: dict[str, float]) -> str:
    """根据有效样本量与关键维度覆盖率动态评估置信度。"""
    if sample_size == 0 or effective_size == 0:
        return "low"
    
    if not field_cov:
        return "high" if effective_size >= 30 else ("medium" if effective_size >= 5 else "low")

    min_cov = min(field_cov.values()) if field_cov else 1.0
    avg_cov = float(sum(field_cov.values()) / len(field_cov)) if field_cov else 1.0

    if min_cov < 0.30 or avg_cov < 0.40 or effective_size < 5:
        return "low"
    elif effective_size >= 30 and avg_cov >= 0.70 and min_cov >= 0.50:
        return "high"
    else:
        return "medium"


# =============================================================================
# 2. 核心独立合成入口 (Pure Insight Synthesizer)
# =============================================================================

def synthesize_from_request(request: dict, analysis_id: str | None = None,
                            generated_at: str | None = None) -> dict[str, Any]:
    """核心独立契约入口：消费标准三元 InsightRequest (v1.2 增强)，输出严格 11 字段 InsightPackage。"""
    validate_insight_request(request)

    aid = analysis_id or f"mousheng-{uuid.uuid4().hex[:12]}"
    gen_at = generated_at if generated_at is not None else datetime.now(timezone.utc).isoformat()
    req_id = request["request_id"]
    intent = request["analysis_intent"]
    evidence = request["evidence_set"]
    quality = request["quality_context"]
    evidence_prov = evidence["evidence_provenance"]

    intent_type = intent["intent"]
    sample_size = evidence["sample_size"]
    effective_size = evidence["effective_sample_size"]
    records = evidence["records"]
    drilldown_keys = evidence.get("drilldown_keys", [])
    field_coverage = quality["field_coverage"]
    completeness = quality.get("completeness_score", 1.0)
    warnings = quality.get("quality_flags", [])

    confidence = assess_confidence(sample_size, effective_size, field_coverage)

    # 1. 组装 request_context (核心块 1)
    request_context = {
        "question": intent["question"],
        "intent": intent_type,
        "target_entity": intent["target_entity"],
        "scope": intent.get("scope", {}),
        "entities": intent.get("entities", []),
        "metrics": intent.get("metrics", []),
        "rules": intent.get("rules", []),
        "ambiguities": intent.get("ambiguities", []),
        "requested_outputs": intent.get("requested_outputs", ["insight", "table"])
    }

    # 2. 组装 provenance (信封 3)
    provenance = {
        "request_id": req_id,
        "insight_component_version": INSIGHT_COMPONENT_VERSION,
        "insight_generated_at": gen_at,
        "evidence_provenance": {
            "dataset_id": evidence_prov["dataset_id"],
            "data_version": evidence_prov["data_version"],
            "ontology_profile_version": evidence_prov["ontology_profile_version"],
            "dataset_mapping_version": evidence_prov.get("dataset_mapping_version"),
            "engine_run_id": evidence_prov.get("engine_run_id"),
            "generated_at": evidence_prov["generated_at"]
        }
    }

    # 3. 空结果特例
    if sample_size == 0 or not records:
        pkg = {
            "analysis_id": aid,
            "artifact_type": "InsightPackage",
            "request_context": request_context,
            "conclusion": {
                "summary": "查询无结果，建议放宽筛选条件或检查字段取值。",
                "confidence_level": "low",
                "verdict": "当前过滤条件下无匹配有效样本"
            },
            "evidence": {
                "sample_size": 0,
                "effective_sample_size": 0,
                "table_preview": [],
                "drilldown_keys": [],
                "out_csv_path": None,
                "chart_spec_ref": None
            },
            "structure": {
                "concentration_metrics": compute_concentration([]),
                "distributions": []
            },
            "differences": {
                "compared_groups": [],
                "metric_contrasts": []
            },
            "anomalies": {
                "baseline_method": "N/A",
                "detected_outliers": []
            },
            "quality_and_limits": {
                "completeness_score": 0.0,
                "field_coverage": field_coverage,
                "applicable_dimensions": quality.get("applicable_dimensions", []),
                "warnings": warnings + ["有效样本为0"],
                "limitations": ["无有效数据支撑，无法进行任何统计与结构性推断"]
            },
            "followups": [
                {"suggested_question": "放宽筛选阈值后重新查询", "analytical_intent": "扩大搜索范围"},
                {"suggested_question": "查看该字段的真实统计画像分布", "analytical_intent": "掌握数据真实取值"}
            ],
            "provenance": provenance
        }
        validate_insight_package(pkg)
        return pkg

    # 4. 分意图类型综合分析
    if intent_type == "rule_ranking" or "达标率" in records[0]:
        conclusion, structure, diffs, anomalies, limits, followups = _synthesize_rule_ranking(records, intent, evidence, confidence, field_coverage)
    elif intent_type in ["distribution_comparison", "concentration_analysis"]:
        conclusion, structure, diffs, anomalies, limits, followups = _synthesize_distribution(records, intent, evidence, confidence)
    else:
        conclusion, structure, diffs, anomalies, limits, followups = _synthesize_detail_list(records, intent, evidence, confidence)

    # 5. 组装标准 11 字段 InsightPackage
    table_preview = records[:10]
    package = {
        "analysis_id": aid,
        "artifact_type": "InsightPackage",
        "request_context": request_context,
        "conclusion": conclusion,
        "evidence": {
            "sample_size": sample_size,
            "effective_sample_size": effective_size,
            "table_preview": table_preview,
            "drilldown_keys": drilldown_keys,
            "out_csv_path": evidence.get("out_csv_path"),
            "chart_spec_ref": evidence.get("chart_spec_ref")
        },
        "structure": structure,
        "differences": diffs,
        "anomalies": anomalies,
        "quality_and_limits": {
            "completeness_score": completeness,
            "field_coverage": field_coverage,
            "applicable_dimensions": quality.get("applicable_dimensions", list(field_coverage.keys())),
            "warnings": warnings,
            "limitations": limits
        },
        "followups": followups,
        "provenance": provenance
    }

    validate_insight_package(package)
    return package


# =============================================================================
# 3. 场景分型提炼逻辑 (Semantic-Driven Synthesizers)
# =============================================================================

def _synthesize_rule_ranking(records: list[dict], intent: dict, evidence: dict, confidence: str, field_cov: dict):
    n = len(records)
    dims = evidence.get("dimensions") or []
    name_col = dims[0]["name"] if dims else next((c for c in ("经营场所名称", "经营场所", "候选") if c in records[0]), list(records[0].keys())[0])
    
    measures = evidence.get("measures") or []
    score_col = next((m["name"] for m in measures if m.get("aggregation") == "score"), None)
    sort_col = score_col or ("达标率" if "达标率" in records[0] else list(records[0].keys())[-1])

    sorted_recs = sorted(records, key=lambda r: float(r.get(sort_col, 0) or 0), reverse=True)
    top_names = [str(r.get(name_col, "")) for r in sorted_recs[:3]]

    count_col = next((c for c in records[0].keys() if "基数" in c or "count" in c.lower()), None)
    counts = [float(r.get(count_col, 1) or 1) for r in sorted_recs]
    conc = compute_concentration(counts)

    grades = {}
    for r in records:
        g = r.get("等级", "")
        if g:
            grades[g] = grades.get(g, 0) + 1
    gd = "、".join(f"{k} {v}" for k, v in grades.items())

    summary = (
        f"在给定的规则下共评估出 {n} 个候选对象"
        + (f"，规则符合度分层分布为：{gd}" if gd else "")
        + (f"；头部符合度较高对象包括：{'、'.join(top_names)}" if top_names else "")
        + "。"
    )

    verdict_map = {
        "high_concentration": "候选呈现明显头部聚集",
        "moderate_concentration": "候选规模分布相对均衡",
        "unconcentrated": "候选分布高度分散"
    }

    conclusion = {
        "summary": summary,
        "confidence_level": confidence,
        "verdict": verdict_map.get(conc["category"], "规则评估已完成")
    }

    structure = {
        "concentration_metrics": conc,
        "distributions": [{
            "dimension": name_col,
            "top_segments": [{"name": str(r[name_col]), "score": float(r.get(sort_col, 0) or 0)} for r in sorted_recs[:5]],
            "long_tail_ratio": round(float(sum(counts[5:]) / sum(counts)), 3) if len(counts) > 5 and sum(counts) > 0 else 0.0
        }]
    }

    diffs = {"compared_groups": top_names[:2], "metric_contrasts": []}
    anomalies = {
        "baseline_method": "Tukey's Fences IQR",
        "detected_outliers": detect_outliers_iqr_from_records(records, sort_col, name_col, name_col)
    }

    limits = []
    if confidence == "low":
        limits.append("样本量不足或数据稀疏度较高，低等级候选评分可能受缺失值影响")
    limits.append("本评估仅衡量候选对象对指定规则的符合度，不构成业务投资或执行动作决策")

    followups = [
        {"suggested_question": "对比头部候选对象的周末客流分布，分析其客流集中度差异", "analytical_intent": "客流集中度对比"},
        {"suggested_question": "查看符合度较低候选的各维度缺失情况，评估数据完整度", "analytical_intent": "数据质量诊断"},
        {"suggested_question": f"下钻查看高符合度候选「{top_names[0]}」内部的具体门店构成明细", "analytical_intent": "明细下钻"} if top_names else {"suggested_question": "下钻分析具体门店", "analytical_intent": "明细下钻"}
    ]

    return conclusion, structure, diffs, anomalies, limits, followups


def _synthesize_distribution(records: list[dict], intent: dict, evidence: dict, confidence: str):
    dims = evidence.get("dimensions") or []
    grp = dims[0]["name"] if dims else (evidence.get("primary_dimension") or list(records[0].keys())[0])

    measures = evidence.get("measures") or []
    cnt_col = measures[0]["name"] if measures else None
    if not cnt_col or cnt_col not in records[0]:
        num_cols = [c for c in records[0].keys() if c != grp and isinstance(records[0][c], (int, float))]
        cnt_col = num_cols[0] if num_cols else None

    unit = measures[0].get("unit", "") if measures else ""

    def _to_f(v):
        try:
            return float(v or 0)
        except (ValueError, TypeError):
            return 0.0

    if cnt_col:
        sorted_recs = sorted(records, key=lambda r: _to_f(r.get(cnt_col, 0)), reverse=True)
        top_name = str(sorted_recs[0].get(grp, ""))
        top_val = int(_to_f(sorted_recs[0].get(cnt_col, 0)))
        total = sum(int(_to_f(r.get(cnt_col, 0))) for r in records)
        counts = [_to_f(r.get(cnt_col, 0)) for r in sorted_recs]
    else:
        sorted_recs = records
        top_name = str(sorted_recs[0].get(grp, ""))
        top_val = len(records)
        total = len(records)
        counts = [1.0] * len(records)

    pct = round(top_val / total * 100, 1) if total else 0.0
    conc = compute_concentration(counts)

    unit_str = f" {unit}" if unit and unit != "数值" else ""
    summary = (
        f"{grp} 维度共 {len(records)} 组，TOP1 为「{top_name}」（{top_val:,}{unit_str}，占 {pct}%）；"
        f"长尾末位为「{sorted_recs[-1].get(grp, '')}」（{int(_to_f(sorted_recs[-1].get(cnt_col, 0))):,}{unit_str}）。"
    )

    grain_desc = f"（粒度: {evidence.get('grain', 'unspecified')}）" if evidence.get("grain") and evidence.get("grain") != "unspecified" else ""
    conclusion = {
        "summary": summary,
        "confidence_level": confidence,
        "verdict": f"{grp} 维度呈现「{top_name}」主导的头部聚集特征与长尾分布差异{grain_desc}"
    }

    structure = {
        "concentration_metrics": conc,
        "distributions": [{
            "dimension": grp,
            "top_segments": [{"name": str(r.get(grp, "")), "count": int(_to_f(r.get(cnt_col, 0)))} for r in sorted_recs[:5]],
            "long_tail_ratio": round(float(sum(counts[5:]) / total), 3) if len(counts) > 5 and total > 0 else 0.0
        }]
    }

    diffs = {
        "compared_groups": [
            top_name,
            str(sorted_recs[-1].get(grp, "")),
        ],
        "metric_contrasts": [
            {
                "metric": cnt_col or "count",
                "group_a_val": float(top_val),
                "group_b_val": float(_to_f(sorted_recs[-1].get(cnt_col, 0))),
                "absolute_diff": float(top_val - int(_to_f(sorted_recs[-1].get(cnt_col, 0)))),
                "relative_diff_pct": round(float(top_val - int(_to_f(sorted_recs[-1].get(cnt_col, 0)))) / total * 100, 1) if total else 0.0,
                "significance_note": "样本分布与显著性检验（Mann-Whitney U + Cohen's d + Placebo 反驳）详见 quality_layer.py 增强模式（P2 阶段可选挂载）",
            }
        ],
    }
    anomalies = {
        "baseline_method": "Tukey's Fences IQR",
        "detected_outliers": detect_outliers_iqr_from_records(records, cnt_col or "", grp, grp) if cnt_col else []
    }
    limits = ["仅反映当前在册售点在各分组间的数量分布，不代表全市场全品牌份额"]

    followups = [
        {"suggested_question": f"下钻分析 TOP1「{top_name}」内部的细分业态与品牌构成", "analytical_intent": "主导分组下钻"},
        {"suggested_question": f"对比各 {grp} 的平均客流与评分，分析群体结构均衡性", "analytical_intent": "多指标交叉对比"}
    ]

    return conclusion, structure, diffs, anomalies, limits, followups


def _synthesize_detail_list(records: list[dict], intent: dict, evidence: dict, confidence: str):
    n = len(records)
    quota_eval = evidence.get("quota_evaluation")
    dim_found, top_name, pct = None, "", 0.0
    for dim in ("规划大区", "渠道组", "省份", "城市", "经营场所"):
        if dim in records[0]:
            counts = {}
            for r in records:
                v = str(r.get(dim, ""))
                if v:
                    counts[v] = counts.get(v, 0) + 1
            if counts:
                sorted_c = sorted(counts.items(), key=lambda x: x[1], reverse=True)
                top_name = sorted_c[0][0]
                pct = round(sorted_c[0][1] / n * 100, 1)
                dim_found = dim
                break

    if quota_eval:
        tq = quota_eval["target_quota"]
        ps = quota_eval["pool_size"]
        ss = quota_eval["selected_size"]
        status = quota_eval["fulfillment_status"]
        gap = quota_eval["gap_count"]
        cutoffs = quota_eval.get("cutoff_thresholds", {})

        cutoff_desc = []
        for k, v in cutoffs.items():
            cutoff_desc.append(f"{k}截断门槛为 {v}")
        cutoff_str = f"（第 {ss} 名入围底线：{'，'.join(cutoff_desc)}）" if cutoff_desc else ""

        if status == "fulfilled":
            overflow_ratio = round(ps / tq, 1) if tq else 1.0
            loc_str = f"；主要集中在「{top_name}」（{pct}%）" if top_name else ""
            summary = f"成功按优先级完成目标配额 {tq:,} 家售点圈选（候选池全量共 {ps:,} 家，供给溢出比 {overflow_ratio}:1）{cutoff_str}{loc_str}。"
            verdict = f"目标配额达成率 100%，已锁定 TOP {tq} 核心潜客"
        else:
            summary = f"目标配额为 {tq:,} 家，但当前严格条件下仅命中 {ss:,} 家，存在 {gap:,} 家目标缺口（达成率 {ss/tq*100:.1f}%）。"
            verdict = f"存在 {gap} 家配额供给缺口，建议放宽约束条件"
    elif dim_found and top_name:
        summary = f"命中 {n:,} 行明细，主要集中在「{top_name}」（{pct}%）。"
        verdict = f"明细样本空间集中于「{top_name}」"
    else:
        summary = f"命中 {n:,} 行明细。"
        verdict = "明细清单提取完成"

    conclusion = {"summary": summary, "confidence_level": confidence, "verdict": verdict}
    structure: dict[str, Any] = {"concentration_metrics": compute_concentration([]), "distributions": []}
    if quota_eval:
        structure["quota_evaluation"] = quota_eval

    diffs = {"compared_groups": [], "metric_contrasts": []}
    anomalies = {"baseline_method": "N/A", "detected_outliers": []}
    limits = ["明细清单受筛选条件与当前数据抓取范围限制，部分稀疏字段可能存在空值"]

    followups = []
    if quota_eval and quota_eval.get("gap_count", 0) > 0:
        followups.append({
            "suggested_question": "放宽评分或客流阈值后重新评估缺口弥补情况",
            "analytical_intent": "缺口灵敏度分析"
        })
    followups.extend([
        {"suggested_question": "按大区或城市维度聚合，观察空间分布形态", "analytical_intent": "空间分布分析"},
        {"suggested_question": "进一步叠加评分或客流阈值，观察高价值子群体规模", "analytical_intent": "多条件深入探索"}
    ])

    return conclusion, structure, diffs, anomalies, limits, followups


def print_insight(insight: dict) -> None:
    """CLI 人类可读打印（展示 8 块结构化洞察提炼，零越界命令）。"""
    if not insight:
        return
    print("\n" + "—" * 60)
    print("【结构化洞察 · 第二棱镜综合输出】")
    concl = insight.get("conclusion", {})
    summary = concl.get("summary", "")
    if summary:
        print(f"  · 核心结论: {summary}")
    
    notice = concl.get("verdict", "")
    if notice:
        print(f"  · 结构特征与定性: {notice}")
    
    qual = insight.get("quality_and_limits", {})
    limits = qual.get("limitations", [])
    if limits:
        print(f"  · 适用边界限制: {'；'.join(limits)}")

    struct = insight.get("structure", {})
    conc = struct.get("concentration_metrics", {})
    if conc.get("hhi", 0) > 0:
        print(f"  · 集中度评估: HHI={conc.get('hhi')} | CR4={conc.get('cr4')} | 评级={conc.get('category')}")

    quota_eval = struct.get("quota_evaluation")
    if quota_eval:
        print(f"  · 配额达成评估: 目标={quota_eval.get('target_quota')} | 实际={quota_eval.get('selected_size')} | 状态={quota_eval.get('fulfillment_status')} | 缺口={quota_eval.get('gap_count')}")

    followups = insight.get("followups", [])
    for i, f in enumerate(followups, 1):
        q_text = f.get("suggested_question") if isinstance(f, dict) else str(f)
        print(f"  · 建议下一步分析({i}): {q_text}")

