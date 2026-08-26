# -*- coding: utf-8 -*-
"""
test_evidence_semantics.py
==========================
Phase B 专项测试套件：验证 Evidence Semantics (v1.2 增强版) 的语义抽取、
显式度量单位与粒度驱动的洞察合成，以及与 v1.1 基础契约的 100% 向后兼容性。
"""
import sys
from pathlib import Path
import pandas as pd
import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from adapters.dataframe_adapter import extract_insight_request
from insight import synthesize_from_request, validate_insight_request, InsightContractError


def test_dataframe_adapter_evidence_semantics_extraction():
    """测试 dataframe_adapter 能够从 DataFrame 自动提取 dimensions, measures, grain 与 time_window。"""
    df = pd.DataFrame([
        {"连锁品牌名称": "瑞幸咖啡", "门店数": 1822, "大众点评餐饮点评评分星级": 4.2},
        {"连锁品牌名称": "星巴克", "门店数": 1192, "大众点评餐饮点评评分星级": 4.5},
        {"连锁品牌名称": "巴比馒头", "门店数": 1056, "大众点评餐饮点评评分星级": 3.8}
    ])
    dsl = {"type": "B", "group_by": ["连锁品牌名称"], "metrics": ["门店数", "大众点评餐饮点评评分星级"], "nl": "品牌分布与评分"}

    req = extract_insight_request(dsl, df)
    validate_insight_request(req)

    ev = req["evidence_set"]
    assert "dimensions" in ev, "必须包含显式 dimensions 列表"
    assert "measures" in ev, "必须包含显式 measures 列表"
    assert ev["grain"] == "brand", f"主维度为连锁品牌名称时 grain 必须推导为 brand，实测: {ev['grain']}"
    assert ev["time_window"]["period_type"] == "snapshot"

    # 检查维度语义
    brand_dim = next((d for d in ev["dimensions"] if d["name"] == "连锁品牌名称"), None)
    assert brand_dim is not None
    assert brand_dim["uri"] == "prism:outlet/Brand"
    assert brand_dim["type"] == "category"
    assert brand_dim["cardinality"] == 3

    # 检查度量语义与单位
    cnt_m = next((m for m in ev["measures"] if m["name"] == "门店数"), None)
    assert cnt_m is not None
    assert cnt_m["uri"] == "prism:metric/OutletCount"
    assert cnt_m["unit"] == "家"
    assert cnt_m["aggregation"] == "count"

    rate_m = next((m for m in ev["measures"] if m["name"] == "大众点评餐饮点评评分星级"), None)
    assert rate_m is not None
    assert rate_m["unit"] == "星"
    assert rate_m["aggregation"] == "mean"


def test_explicit_semantics_driven_synthesis():
    """测试显式度量单位与 grain 元数据能够精准驱动第二棱镜结论生成。"""
    req = {
        "request_id": "req-sem-001",
        "analysis_intent": {
            "question": "目标大区各省份餐饮售点月均收入潜力对比",
            "intent": "distribution_comparison",
            "target_entity": "prism:outlet/AdministrativeRegion"
        },
        "evidence_set": {
            "sample_size": 3000,
            "effective_sample_size": 3000,
            "primary_dimension": "省份",
            "dimensions": [
                {"name": "省份", "uri": "prism:outlet/AdministrativeRegion", "type": "spatial", "cardinality": 3}
            ],
            "measures": [
                {
                    "name": "月均收入潜力预测(无协议无冰柜)",
                    "uri": "prism:estimate/RevenuePotentialNoAgreementNoCooler",
                    "unit": "元/月",
                    "aggregation": "derived",
                    "data_type": "currency"
                }
            ],
            "grain": "administrative_region",
            "time_window": {
                "start": "2026-08-01",
                "end": "2026-08-25",
                "period_type": "snapshot",
                "as_of_date": "2026-08-25"
            },
            "records": [
                {"省份": "目标城市", "月均收入潜力预测(无协议无冰柜)": 85000},
                {"省份": "目标省份省", "月均收入潜力预测(无协议无冰柜)": 62000},
                {"省份": "目标省份省", "月均收入潜力预测(无协议无冰柜)": 59000}
            ],
            "drilldown_keys": [{"省份": "目标城市"}],
            "evidence_provenance": {
                "dataset_id": "outlet-data-profile",
                "data_version": "2026.08",
                "ontology_profile_version": "0.1.0-rc2",
                "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {
            "completeness_score": 1.0,
            "field_coverage": {"省份": 1.0, "月均收入潜力预测(无协议无冰柜)": 1.0}
        }
    }

    pkg = synthesize_from_request(req, analysis_id="test-sem-001", generated_at="2026-08-25T12:00:00Z")

    # 验证显式单位 "元/月" 注入结论
    summary = pkg["conclusion"]["summary"]
    assert "85,000 元/月" in summary, f"总结陈述中必须携带显式度量单位 '元/月'，实测: {summary}"
    assert "59,000 元/月" in summary, f"长尾末位陈述中必须携带显式度量单位 '元/月'，实测: {summary}"

    # 验证 grain 注入 verdict
    verdict = pkg["conclusion"]["verdict"]
    assert "administrative_region" in verdict, f"定性陈述中必须携带显式 grain 粒度，实测: {verdict}"


def test_backward_compatibility_with_v11_request():
    """测试完全兼容不带 dimensions / measures / grain 的 v1.1 基础 InsightRequest。"""
    req_v11 = {
        "request_id": "req-v11-compat",
        "analysis_intent": {
            "question": "餐饮业态大区分布",
            "intent": "distribution_comparison",
            "target_entity": "prism:outlet/Territory"
        },
        "evidence_set": {
            "sample_size": 1000,
            "effective_sample_size": 1000,
            "primary_dimension": "规划大区",
            "records": [
                {"规划大区": "目标子区域A", "门店数": 600},
                {"规划大区": "目标子区域B", "门店数": 400}
            ],
            "drilldown_keys": [{"规划大区": "目标子区域A"}],
            "evidence_provenance": {
                "dataset_id": "outlet-data-profile",
                "data_version": "2026.08",
                "ontology_profile_version": "0.1.0-rc2",
                "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {
            "completeness_score": 1.0,
            "field_coverage": {"规划大区": 1.0, "门店数": 1.0}
        }
    }

    # 必须能够平滑通过校验并合成
    validate_insight_request(req_v11)
    pkg = synthesize_from_request(req_v11)
    assert pkg["artifact_type"] == "InsightPackage"
    assert "目标子区域A" in pkg["conclusion"]["summary"]
    assert pkg["structure"]["concentration_metrics"]["hhi"] == 5200.0


def test_negative_schema_violations():
    """测试非法 Evidence Semantics 字段在编译期被拦截。"""
    # 非法 grain 类型
    bad_grain_req = {
        "request_id": "req-bad-grain",
        "analysis_intent": {"question": "q", "intent": "detail_list", "target_entity": "prism:outlet/Outlet"},
        "evidence_set": {
            "sample_size": 10,
            "effective_sample_size": 10,
            "grain": "illegal_grain_type",  # 非法枚举
            "records": [{"a": 1}],
            "evidence_provenance": {
                "dataset_id": "d1", "data_version": "v1", "ontology_profile_version": "0.1.0-rc2",
                "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {"field_coverage": {"a": 1.0}}
    }
    with pytest.raises(InsightContractError):
        validate_insight_request(bad_grain_req)


if __name__ == "__main__":
    test_dataframe_adapter_evidence_semantics_extraction()
    test_explicit_semantics_driven_synthesis()
    test_backward_compatibility_with_v11_request()
    test_negative_schema_violations()
    print(">>> All 4 Phase B Evidence Semantics tests passed! <<<")
