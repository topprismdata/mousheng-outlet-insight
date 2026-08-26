# -*- coding: utf-8 -*-
"""
test_priority_quota.py
======================
业务目标配额与优先级阶梯 (Quota Target & Priority Funnel) 专项测试套件。
测试以下真实业务场景：
1. 冰柜投放配额选点（目标 200 家，按潜力降序，提取第 200 名潜力截断线）
2. 餐饮新品铺货圈选（目标 100 家，优先餐饮街，按客流降序）
3. 独立住宿攻坚名单（目标 50 家，按客房数降序，提炼大体量截断线）
4. 严苛条件缺口诊断（目标 50 家，严苛过滤后仅命中少量，识别 gap_detected 并输出缺口建议）
5. 门槛截断与空间集中度洞察综合
"""
import sys
from pathlib import Path
import pandas as pd
import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from agent_core import FieldRegistry, validate_query, run_query, load_store_data
from nl_parser import auto_parse
from adapters.dataframe_adapter import extract_insight_request
from insight import synthesize_from_request, validate_insight_package, InsightContractError


@pytest.fixture(scope="module")
def shared_data():
    reg = FieldRegistry.load()
    df = load_store_data()
    return reg, df


def test_freezer_quota_selection_200(shared_data):
    """测试场景 A：冰柜投放配额选点（目标 200 家未服务食杂店，按潜力降序）。"""
    reg, df = shared_data
    q_text = "在未服务食杂店中选200家投放冰柜，按月均潜力排序"
    dsl = auto_parse(q_text, reg)

    assert dsl["limit"] == 200
    assert dsl["target_quota"] == 200

    res_df, trace, kind, audit_rep = run_query(df, dsl, reg)
    assert len(res_df) == 200

    req = extract_insight_request(dsl, res_df, audit_rep)
    assert req["evidence_set"]["quota_evaluation"]["target_quota"] == 200
    assert req["evidence_set"]["quota_evaluation"]["selected_size"] == 200
    assert req["evidence_set"]["quota_evaluation"]["fulfillment_status"] == "fulfilled"

    pkg = synthesize_from_request(req)
    validate_insight_package(pkg)

    # 验证洞察总结中包含配额达成与溢出比
    summary = pkg["conclusion"]["summary"]
    assert "200" in summary
    assert "达成率 100%" in pkg["conclusion"]["verdict"]
    assert "quota_evaluation" in pkg["structure"]


def test_fmcg_food_quota_selection_100(shared_data):
    """测试场景 B：餐饮新品铺货圈选（目标 100 家，优先餐饮街，按客流降序）。"""
    reg, df = shared_data
    q_text = "目标子区域A圈选100家餐饮店，优先餐饮街，按周末客流降序"
    dsl = auto_parse(q_text, reg)

    assert dsl["limit"] == 100
    assert dsl["target_quota"] == 100

    res_df, trace, kind, audit_rep = run_query(df, dsl, reg)
    assert len(res_df) == 100

    req = extract_insight_request(dsl, res_df, audit_rep)
    pkg = synthesize_from_request(req)
    validate_insight_package(pkg)

    # 验证第 100 名截断门槛线
    quota_struct = pkg["structure"]["quota_evaluation"]
    assert quota_struct["selected_size"] == 100
    assert quota_struct["fulfillment_status"] == "fulfilled"
    assert "周末均值客流(人)" in quota_struct["cutoff_thresholds"]


def test_hotel_ka_quota_selection_50(shared_data):
    """测试场景 C：大客户攻坚名单（目标 50 家非连锁酒店，按客房数降序）。"""
    reg, df = shared_data
    q_text = "挑选50家非连锁酒店，按客房数降序"
    dsl = auto_parse(q_text, reg)

    assert dsl["limit"] == 50
    res_df, trace, kind, audit_rep = run_query(df, dsl, reg)
    assert len(res_df) == 50

    req = extract_insight_request(dsl, res_df, audit_rep)
    pkg = synthesize_from_request(req)
    validate_insight_package(pkg)

    assert pkg["structure"]["quota_evaluation"]["selected_size"] == 50
    assert "携程-房间数" in pkg["structure"]["quota_evaluation"]["cutoff_thresholds"]


def test_strict_condition_gap_detection(shared_data):
    """测试场景 D：严苛条件缺口诊断（目标 500 家，但严苛条件仅命中极少，识别 gap_detected 并建议放宽）。"""
    reg, df = shared_data
    # 极严苛条件：客房 > 300 间的大型酒店
    q_text = "选500家客房大于300间的酒店"
    dsl = auto_parse(q_text, reg)

    res_df, trace, kind, audit_rep = run_query(df, dsl, reg)
    actual_count = len(res_df)
    assert actual_count < 500  # 存在缺口

    req = extract_insight_request(dsl, res_df, audit_rep)
    assert req["evidence_set"]["quota_evaluation"]["fulfillment_status"] == "gap_detected"
    assert req["evidence_set"]["quota_evaluation"]["gap_count"] == 500 - actual_count

    pkg = synthesize_from_request(req)
    validate_insight_package(pkg)

    summary = pkg["conclusion"]["summary"]
    assert "目标缺口" in summary or "存在" in summary
    assert "配额供给缺口" in pkg["conclusion"]["verdict"]
    # 验证追问中包含缺口灵敏度分析
    followup_intents = [f["analytical_intent"] for f in pkg["followups"]]
    assert "缺口灵敏度分析" in followup_intents


if __name__ == "__main__":
    reg = FieldRegistry.load()
    df = load_store_data()
    data = (reg, df)
    test_freezer_quota_selection_200(data)
    test_fmcg_food_quota_selection_100(data)
    test_hotel_ka_quota_selection_50(data)
    test_strict_condition_gap_detection(data)
    print(">>> All 4 Priority Quota & Gap Analysis tests passed! <<<")
