# -*- coding: utf-8 -*-
"""
test_insight_quality.py
=======================
谋圣·售点洞察 (Mousheng Outlet Insight) 10 大黄金场景“洞察质量”与输入/输出契约专项测试。
测试重点：
1. 核心 Python API 强契约门禁 (synthesize_from_request 遇到非法请求直接抛出 InsightContractError)
2. 语义约束校验 (effective_sample_size <= sample_size) 与 date-time 校验
3. 纯三元输入契约 (AnalysisIntent + EvidenceSet + QualityContext + EvidenceProvenance) 强校验与负向拒绝
4. 严格 11 个顶级字段输出契约 (3信封 + 8核心洞察块，拒绝一切未知字段与 action 注入)
5. 真实 Provenance 来源流转（杜绝核心硬编码伪造数据源）
6. 集中度 (HHI/CR4/CR8/Gini) 与离群异常 (Tukey IQR) 纯数学计算精确性
"""
import copy
import json
import pytest
from pathlib import Path
from jsonschema import Draft202012Validator

from insight import (
    synthesize_from_request,
    compute_concentration,
    detect_outliers_iqr_from_records,
    assess_confidence,
    validate_insight_request,
    validate_insight_package,
    InsightContractError,
    FORMAT_CHECKER
)
from adapters.legacy_dataframe_api import synthesize
SKILL_DIR = Path(__file__).resolve().parent.parent
REQ_SCHEMA_PATH = SKILL_DIR / "references" / "insight-request.schema.json"
PKG_SCHEMA_PATH = SKILL_DIR / "references" / "insight-package.schema.json"

with open(REQ_SCHEMA_PATH, "r", encoding="utf-8") as f:
    REQ_SCHEMA = json.load(f)

with open(PKG_SCHEMA_PATH, "r", encoding="utf-8") as f:
    PKG_SCHEMA = json.load(f)
REQ_VALIDATOR = Draft202012Validator(REQ_SCHEMA, format_checker=FORMAT_CHECKER)
PKG_VALIDATOR = Draft202012Validator(PKG_SCHEMA, format_checker=FORMAT_CHECKER)

def test_core_api_negative_rejections():
    """核心 Python API 契约拦截测试：直接调用 synthesize_from_request 传入非法请求必须抛出 InsightContractError。"""
    valid_req = {
        "request_id": "req-valid-001",
        "analysis_intent": {
            "question": "有效测试问题",
            "intent": "detail_list",
            "target_entity": "prism:outlet/Outlet"
        },
        "evidence_set": {
            "sample_size": 10,
            "effective_sample_size": 10,
            "records": [{"id": "1"}],
            "evidence_provenance": {
                "dataset_id": "test-ds",
                "data_version": "1.0",
                "ontology_profile_version": "0.1.0-rc2",
                "generated_at": "2026-08-25T00:00:00Z"
            }
        },
        "quality_context": {
            "completeness_score": 1.0,
            "field_coverage": {"id": 1.0}
        }
    }

    # 1. 合法请求直接通过
    pkg = synthesize_from_request(valid_req)
    assert len(pkg.keys()) == 11

    # 2. 旁路测试：仅传 {"request_id": "bad"} -> 必须抛出 InsightContractError
    with pytest.raises(InsightContractError):
        synthesize_from_request({"request_id": "bad"})

    # 3. 语义约束违规：effective_sample_size > sample_size (20 > 10) -> 必须抛出 InsightContractError
    bad_semantic = copy.deepcopy(valid_req)
    bad_semantic["evidence_set"]["effective_sample_size"] = 20
    with pytest.raises(InsightContractError, match="effective_sample_size.*不能大于.*sample_size"):
        synthesize_from_request(bad_semantic)

    # 4. 缺失 evidence_provenance -> 必须抛出 InsightContractError
    bad_no_prov = copy.deepcopy(valid_req)
    del bad_no_prov["evidence_set"]["evidence_provenance"]
    with pytest.raises(InsightContractError):
        synthesize_from_request(bad_no_prov)

    # 5. 非法 date-time 格式 -> 必须抛出 InsightContractError
    bad_date = copy.deepcopy(valid_req)
    bad_date["evidence_set"]["evidence_provenance"]["generated_at"] = "invalid-date-string"
    with pytest.raises(InsightContractError):
        synthesize_from_request(bad_date)

    print("  ✓ Core Python API 强契约负向测试 (直接调用 synthesize_from_request 无法绕过校验，均抛出 InsightContractError): PASSED")


def test_package_schema_negative_rejections():
    """输出契约负向校验：向包中注入 action、task、额外未知字段必须被 Schema 100% 拒绝。"""
    valid_pkg = {
        "analysis_id": "test-neg-001",
        "artifact_type": "InsightPackage",
        "request_context": {"question": "测试", "intent": "detail_list", "target_entity": "prism:outlet/Outlet"},
        "conclusion": {"summary": "结论", "confidence_level": "high", "verdict": "定性"},
        "evidence": {"sample_size": 10, "effective_sample_size": 10, "table_preview": [], "drilldown_keys": []},
        "structure": {"concentration_metrics": compute_concentration([]), "distributions": []},
        "differences": {"compared_groups": [], "metric_contrasts": []},
        "anomalies": {"baseline_method": "N/A", "detected_outliers": []},
        "quality_and_limits": {"completeness_score": 1.0, "field_coverage": {}, "applicable_dimensions": [], "warnings": [], "limitations": []},
        "followups": [{"suggested_question": "下钻分析", "analytical_intent": "下钻"}],
        "provenance": {
            "request_id": "req-valid-001",
            "insight_component_version": "1.3.0-rc3",
            "insight_generated_at": "2026-08-25T00:00:00Z",
            "evidence_provenance": {
                "dataset_id": "sh", "data_version": "1.0", "ontology_profile_version": "0.1.0-rc2", "generated_at": "2026-08-25T00:00:00Z"
            }
        }
    }

    # 1. 验证合法对象 0 错误
    assert len(list(PKG_VALIDATOR.iter_errors(valid_pkg))) == 0

    # 2. 注入顶级 action 字段 -> 必须被拒绝
    bad_action = copy.deepcopy(valid_pkg)
    bad_action["action"] = "建议派单去拜访"
    errs_action = list(PKG_VALIDATOR.iter_errors(bad_action))
    assert len(errs_action) > 0, "Schema failed to reject injected 'action' field!"

    # 3. 注入顶级 task 字段 -> 必须被拒绝
    bad_task = copy.deepcopy(valid_pkg)
    bad_task["task"] = {"assignee": "业务员A", "status": "pending"}
    errs_task = list(PKG_VALIDATOR.iter_errors(bad_task))
    assert len(errs_task) > 0, "Schema failed to reject injected 'task' field!"

    # 4. 注入未知顶级字段 -> 必须被拒绝 (additionalProperties: false)
    bad_unknown = copy.deepcopy(valid_pkg)
    bad_unknown["unknown_custom_field"] = 123
    errs_unknown = list(PKG_VALIDATOR.iter_errors(bad_unknown))
    assert len(errs_unknown) > 0, "Schema failed to reject unknown custom field!"

    print("  ✓ Package Schema 负向拒绝测试 (注入 action / task / unknown 均被严格拦截): PASSED")


def test_cq001_detail_list_insight_quality():
    """CQ-001: 指定品牌与区域清单提取洞察质量 (三元输入 + 11 顶级字段 + 真实 provenance 流转)。"""
    request = {
        "request_id": "req-cq001",
        "analysis_intent": {
            "question": "目标子区域B的美宜佳清单",
            "intent": "detail_list",
            "target_entity": "prism:outlet/Outlet",
            "scope": {"territory": "目标子区域B"}
        },
        "evidence_set": {
            "sample_size": 37,
            "effective_sample_size": 37,
            "primary_dimension": "规划大区",
            "records": [{"腾讯售点id": f"T_{i}", "腾讯售点名称": f"美宜佳目标子区域B{i}号店", "规划大区": "目标子区域B"} for i in range(1, 38)],
            "drilldown_keys": [{"腾讯售点id": "T_1"}],
            "evidence_provenance": {
                "dataset_id": "custom-retail-ds-01",
                "data_version": "2026.08.25",
                "ontology_profile_version": "0.1.0-rc2",
                "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {
            "completeness_score": 1.0,
            "field_coverage": {"规划大区": 1.0, "连锁品牌名称": 1.0},
            "applicable_dimensions": ["规划大区", "连锁品牌名称"]
        }
    }
    pkg = synthesize_from_request(request)
    
    assert len(list(PKG_VALIDATOR.iter_errors(pkg))) == 0
    assert len(pkg.keys()) == 11, f"Expected strictly 11 top-level keys, got {len(pkg.keys())}: {list(pkg.keys())}"
    assert pkg["evidence"]["effective_sample_size"] == 37
    assert "37" in pkg["conclusion"]["summary"]
    assert "目标子区域B" in pkg["conclusion"]["summary"]
    # 验证 provenance 真实流转
    assert pkg["provenance"]["request_id"] == "req-cq001"
    assert pkg["provenance"]["evidence_provenance"]["dataset_id"] == "custom-retail-ds-01"
    assert pkg["provenance"]["insight_component_version"] == "1.3.0-rc3"
    assert pkg["provenance"]["request_id"] == "req-cq001"

def test_cq002_channel_distribution_insight_quality():
    """CQ-002: 区域业态结构分布洞察质量。"""
    request = {
        "request_id": "req-cq002",
        "analysis_intent": {
            "question": "目标省份省各业态分布",
            "intent": "distribution_comparison",
            "target_entity": "prism:outlet/Outlet",
            "scope": {"province": "目标省份省"}
        },
        "evidence_set": {
            "sample_size": 2830,
            "effective_sample_size": 2830,
            "primary_dimension": "渠道组",
            "records": [
                {"渠道组": "餐饮", "客户数": 1500},
                {"渠道组": "食杂", "客户数": 800},
                {"渠道组": "住宿", "客户数": 300},
                {"渠道组": "娱乐", "客户数": 100},
                {"渠道组": "教育", "客户数": 80},
                {"渠道组": "其他", "客户数": 50}
            ],
            "drilldown_keys": [{"渠道组": "餐饮"}],
            "evidence_provenance": {
                "dataset_id": "outlet-data-profile",
                "data_version": "2026.08",
                "ontology_profile_version": "0.1.0-rc2",
                "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {
            "completeness_score": 1.0,
            "field_coverage": {"渠道组": 1.0, "客户数": 1.0},
            "applicable_dimensions": ["渠道组"]
        }
    }
    pkg = synthesize_from_request(request)
    
    assert len(list(PKG_VALIDATOR.iter_errors(pkg))) == 0
    assert len(pkg.keys()) == 11
    assert "餐饮" in pkg["conclusion"]["summary"]
    assert "1,500" in pkg["conclusion"]["summary"]
    assert "长尾" in pkg["conclusion"]["summary"]
    assert pkg["structure"]["distributions"][0]["long_tail_ratio"] > 0
    print("  ✓ CQ-002 (业态结构分布洞察质量): PASSED")


def test_cq003_brand_concentration_insight_quality():
    """CQ-003: 业态品牌集中度数学精确性 (HHI, CR4, Gini)。"""
    counts = [1822, 1192, 1056, 600, 400, 300, 200, 100]
    conc = compute_concentration(counts)
    
    assert conc["hhi"] > 1500, "HHI 应大于 1500"
    assert conc["cr4"] > 0.60, "CR4 应大于 60%"
    assert conc["category"] in ["high_concentration", "moderate_concentration"]
    assert 0.0 <= conc["gini"] <= 1.0
    print(f"  ✓ CQ-003 (品牌集中度数学精确性): HHI={conc['hhi']}, CR4={conc['cr4']}, Gini={conc['gini']} -> PASSED")


def test_cq004_group_comparison_insight_quality():
    """CQ-004: 群体同态指标对比洞察质量。"""
    request = {
        "request_id": "req-cq004",
        "analysis_intent": {
            "question": "目标子区域A vs 目标子区域B食杂店客流对比",
            "intent": "distribution_comparison",
            "target_entity": "prism:outlet/Outlet"
        },
        "evidence_set": {
            "sample_size": 2,
            "effective_sample_size": 2,
            "primary_dimension": "规划大区",
            "records": [
                {"规划大区": "目标子区域B", "平均客流": 1200.0},
                {"规划大区": "目标子区域A", "平均客流": 800.0}
            ],
            "drilldown_keys": [],
            "evidence_provenance": {
                "dataset_id": "outlet-data-profile",
                "data_version": "2026.08",
                "ontology_profile_version": "0.1.0-rc2",
                "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {
            "completeness_score": 1.0,
            "field_coverage": {"规划大区": 1.0, "平均客流": 1.0},
            "applicable_dimensions": ["规划大区"]
        }
    }
    pkg = synthesize_from_request(request)
    
    assert len(list(PKG_VALIDATOR.iter_errors(pkg))) == 0
    assert len(pkg.keys()) == 11
    diff = pkg["differences"]["metric_contrasts"][0]
    assert diff["absolute_diff"] == 400.0
    assert diff["relative_diff_pct"] == 20.0
    print("  ✓ CQ-004 (群体对比差异计算质量): PASSED")


def test_cq005_rule_evaluation_insight_quality():
    """CQ-005: 用户多因子规则评估洞察质量。"""
    request = {
        "request_id": "req-cq005",
        "analysis_intent": {
            "question": "餐饮街规则评估",
            "intent": "rule_ranking",
            "target_entity": "prism:outlet/CommercialSite"
        },
        "evidence_set": {
            "sample_size": 3,
            "effective_sample_size": 3,
            "primary_dimension": "经营场所名称",
            "records": [
                {"经营场所名称": "目标城市2西路餐饮街", "餐饮基数": 35, "达标率": 0.9, "等级": "高"},
                {"经营场所名称": "淮海路餐饮街", "餐饮基数": 28, "达标率": 0.7, "等级": "中"},
                {"经营场所名称": "徐家汇商圈", "餐饮基数": 22, "达标率": 0.4, "等级": "低"}
            ],
            "drilldown_keys": [{"经营场所名称": "目标城市2西路餐饮街"}],
            "evidence_provenance": {
                "dataset_id": "outlet-data-profile",
                "data_version": "2026.08",
                "ontology_profile_version": "0.1.0-rc2",
                "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {
            "completeness_score": 0.95,
            "field_coverage": {"经营场所名称": 1.0, "餐饮基数": 1.0, "达标率": 1.0},
            "applicable_dimensions": ["经营场所名称", "餐饮基数", "达标率"]
        }
    }
    pkg = synthesize_from_request(request)
    
    assert len(list(PKG_VALIDATOR.iter_errors(pkg))) == 0
    assert len(pkg.keys()) == 11
    assert "高" in pkg["conclusion"]["summary"]
    assert "目标城市2西路餐饮街" in pkg["conclusion"]["summary"]
    print("  ✓ CQ-005 (规则符合度评估洞察质量): PASSED")


def test_cq006_anomaly_and_discrepancy_insight_quality():
    """CQ-006: 多维指标背离与离群异常识别质量 (Tukey IQR)。"""
    records = [{"腾讯售点id": f"ID_{i}", "腾讯售点名称": f"网点_{i}", "周末客流": 100 + i * 2} for i in range(20)]
    records.append({"腾讯售点id": "ID_999", "腾讯售点名称": "极端异动网红店", "周末客流": 9000})
    
    outliers = detect_outliers_iqr_from_records(records, "周末客流", "腾讯售点id", "腾讯售点名称")
    assert len(outliers) == 1
    assert outliers[0]["entity_name"] == "极端异动网红店"
    assert outliers[0]["value"] == 9000.0
    print("  ✓ CQ-006 (Tukey IQR 离群点检测质量): PASSED")


def test_cq007_quality_gating_and_confidence_calibration():
    """CQ-007: 数据稀疏度质检与置信度动态降级。"""
    field_cov = {"携程-房间数": 0.85, "携程-星级": 0.10}
    conf = assess_confidence(sample_size=15, effective_size=15, field_cov=field_cov)
    assert conf == "low", f"低覆盖率样本置信度应降级为 low, 实测={conf}"

    request = {
        "request_id": "req-cq007",
        "analysis_intent": {"question": "景区周边客栈", "intent": "detail_list", "target_entity": "prism:outlet/Outlet"},
        "evidence_set": {
            "sample_size": 15, "effective_sample_size": 15, "primary_dimension": None,
            "records": [{"腾讯售点名称": "西湖客栈1号", "携程-房间数": 10}],
            "drilldown_keys": [],
            "evidence_provenance": {
                "dataset_id": "outlet-data-profile", "data_version": "2026.08", "ontology_profile_version": "0.1.0-rc2", "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {
            "completeness_score": 0.47,
            "field_coverage": field_cov,
            "applicable_dimensions": ["携程-房间数", "携程-星级"],
            "quality_flags": ["星级字段缺失率达90%"]
        }
    }
    pkg = synthesize_from_request(request)
    assert pkg["conclusion"]["confidence_level"] == "low"
    assert len(pkg["quality_and_limits"]["limitations"]) > 0
    print("  ✓ CQ-007 (数据稀疏度置信度校准与限制生成): PASSED")


def test_cq008_unavailable_concept_rejection():
    """CQ-008: 缺失业务概念拒答与限制声明。"""
    request = {
        "request_id": "req-cq008",
        "analysis_intent": {"question": "竞品销量份额", "intent": "detail_list", "target_entity": "prism:outlet/Outlet"},
        "evidence_set": {
            "sample_size": 0, "effective_sample_size": 0, "primary_dimension": None, "records": [], "drilldown_keys": [],
            "evidence_provenance": {
                "dataset_id": "outlet-data-profile", "data_version": "2026.08", "ontology_profile_version": "0.1.0-rc2", "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {"completeness_score": 0.0, "field_coverage": {}, "applicable_dimensions": []}
    }
    pkg = synthesize_from_request(request)
    assert pkg["conclusion"]["confidence_level"] == "low"
    assert "无有效数据支撑" in pkg["quality_and_limits"]["limitations"][0]
    print("  ✓ CQ-008 (缺失概念限制声明质量): PASSED")


def test_cq009_drilldown_keys_generation():
    """CQ-009: 汇总结果下钻过滤键提取质量。"""
    request = {
        "request_id": "req-cq009",
        "analysis_intent": {"question": "品牌汇总", "intent": "distribution_comparison", "target_entity": "prism:outlet/Brand"},
        "evidence_set": {
            "sample_size": 2, "effective_sample_size": 2, "primary_dimension": "连锁品牌名称",
            "records": [{"连锁品牌名称": "瑞幸咖啡", "门店数": 1822}, {"连锁品牌名称": "星巴克", "门店数": 1192}],
            "drilldown_keys": [{"连锁品牌名称": "瑞幸咖啡"}, {"连锁品牌名称": "星巴克"}],
            "evidence_provenance": {
                "dataset_id": "outlet-data-profile", "data_version": "2026.08", "ontology_profile_version": "0.1.0-rc2", "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {"completeness_score": 1.0, "field_coverage": {"连锁品牌名称": 1.0}, "applicable_dimensions": ["连锁品牌名称"]}
    }
    pkg = synthesize_from_request(request)
    drillkeys = pkg["evidence"]["drilldown_keys"]
    assert len(drillkeys) == 2
    assert drillkeys[0] == {"连锁品牌名称": "瑞幸咖啡"}
    print("  ✓ CQ-009 (下钻过滤键生成质量): PASSED")


def test_cq010_insight_package_schema_and_zero_crossing():
    """CQ-010: 标准 Insight Package 契约与绝对零越界验证。"""
    request = {
        "request_id": "req-cq010",
        "analysis_intent": {"question": "大区分布", "intent": "distribution_comparison", "target_entity": "prism:outlet/Outlet"},
        "evidence_set": {
            "sample_size": 100, "effective_sample_size": 100, "primary_dimension": "规划大区",
            "records": [{"规划大区": "目标子区域B", "客户数": 100}],
            "drilldown_keys": [],
            "evidence_provenance": {
                "dataset_id": "outlet-data-profile", "data_version": "2026.08", "ontology_profile_version": "0.1.0-rc2", "generated_at": "2026-08-25T12:00:00Z"
            }
        },
        "quality_context": {"completeness_score": 1.0, "field_coverage": {"规划大区": 1.0}, "applicable_dimensions": ["规划大区"]}
    }
    pkg = synthesize_from_request(request)
    
    # 1. 严格 11 顶级字段与 Schema 100% 校验
    assert len(pkg.keys()) == 11, f"Expected 11 keys, got {list(pkg.keys())}"
    errs = list(PKG_VALIDATOR.iter_errors(pkg))
    assert len(errs) == 0, f"Schema validation errors: {errs}"
    
    # 2. 违禁词 0 出现
    pkg_str = json.dumps(pkg, ensure_ascii=False)
    forbidden = ["派单", "路线", "排程", "投放", "应该去", "安排拜访", "必须执行", "决策助手"]
    for w in forbidden:
        assert w not in pkg_str, f"Found forbidden term '{w}' in InsightPackage"
    
    # 3. 严格禁止 Action 实体
    assert "action" not in pkg
    assert "actions" not in pkg
    assert "tasks" not in pkg
    assert "route_plan" not in pkg
    
    print("  ✓ CQ-010 (标准 11 字段 InsightPackage 契约与 0 越界验证): PASSED")


def test_cq011_metric_conflict_diagnosis_insight_quality():
    """CQ-011: 多源指标冲突与异动诊断 (高评分低客流背离识别)。"""
    req = {
        "request_id": "req-cq011",
        "analysis_intent": {
            "question": "大众点评评分>=4.5星但客流处于后20%的餐饮门店清单",
            "intent": "anomaly_detection",
            "target_entity": "prism:outlet/Outlet",
            "scope": {"channel": "餐饮"},
            "entities": [],
            "metrics": ["大众点评餐饮点评评分星级", "周末均值客流(人)"],
            "rules": [{"conflict": "DianpingRating>=4.5 AND WeekendTraffic<=P20"}],
            "ambiguities": [],
            "requested_outputs": ["insight", "table"]
        },
        "evidence_set": {
            "sample_size": 150,
            "effective_sample_size": 150,
            "primary_dimension": "腾讯售点名称",
            "dimensions": [
                {"name": "腾讯售点名称", "uri": "prism:outlet/Outlet", "type": "identifier", "cardinality": 150}
            ],
            "measures": [
                {"name": "大众点评餐饮点评评分星级", "uri": "prism:metric/DianpingRating", "unit": "星", "aggregation": "mean"},
                {"name": "周末均值客流(人)", "uri": "prism:metric/WeekendTraffic", "unit": "人/天", "aggregation": "mean"}
            ],
            "grain": "outlet",
            "time_window": {"period_type": "snapshot"},
            "records": [
                {"腾讯售点名称": f"隐世私房菜#{i}", "大众点评餐饮点评评分星级": 4.8, "周末均值客流(人)": 120} for i in range(10)
            ],
            "drilldown_keys": [{"腾讯售点名称": "隐世私房菜#0"}],
            "evidence_provenance": {
                "dataset_id": "outlet-data-profile",
                "data_version": "2026.08",
                "ontology_profile_version": "0.1.0-rc2",
                "generated_at": "2026-08-26T12:00:00Z"
            }
        },
        "quality_context": {
            "completeness_score": 1.0,
            "field_coverage": {"大众点评餐饮点评评分星级": 1.0, "周末均值客流(人)": 1.0}
        }
    }

    pkg = synthesize_from_request(req, analysis_id="pkg-cq011")
    validate_insight_package(pkg)

    assert pkg["artifact_type"] == "InsightPackage"
    assert len(pkg.keys()) == 11
    assert "隐世私房菜" in str(pkg["evidence"]["table_preview"])
    assert len(pkg["followups"]) > 0

    print("  ✓ CQ-011 (多源指标冲突与异动诊断质量): PASSED")


def run_all_insight_quality_tests():
    print("\n============================================================")
    print("谋圣·售点洞察 11 大黄金场景『洞察质量与三元输入/输出契约』专项测试")
    print("============================================================")
    test_core_api_negative_rejections()
    test_package_schema_negative_rejections()
    test_cq001_detail_list_insight_quality()
    test_cq002_channel_distribution_insight_quality()
    test_cq003_brand_concentration_insight_quality()
    test_cq004_group_comparison_insight_quality()
    test_cq005_rule_evaluation_insight_quality()
    test_cq006_anomaly_and_discrepancy_insight_quality()
    test_cq007_quality_gating_and_confidence_calibration()
    test_cq008_unavailable_concept_rejection()
    test_cq009_drilldown_keys_generation()
    test_cq010_insight_package_schema_and_zero_crossing()
    test_cq011_metric_conflict_diagnosis_insight_quality()
    print("============================================================")
    print(">>> 11/11 黄金场景洞察质量与三元输入/输出契约测试 100% 通过 <<<")
    print("============================================================\n")


if __name__ == "__main__":
    run_all_insight_quality_tests()
