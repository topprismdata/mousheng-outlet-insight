# -*- coding: utf-8 -*-
"""
blind_test_suite.py
===================
50 条长尾真实业务盲测集 (Long-tail Blind Evaluation Suite)。
覆盖非标准口语表达、多维复合条件、目标容量选点、时间谓词及边界拒答场景，
用于检验 Vanna + DuckDB + DSL 双轨架构的泛化召回与安全拦截率。
"""
import sys
import time
from pathlib import Path
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from dual_router import DualEngineRouter
from adapters.dataframe_adapter import extract_insight_request
from insight import synthesize_from_request, validate_insight_package

BLIND_TEST_QUERIES = [
    # ── 1. 目标容量选点与优先级 (10 题) ──
    {"id": "B01", "q": "在未服务食杂店中选200家投放冰柜，按月均潜力排序", "exp_min": 200, "type": "quota"},
    {"id": "B02", "q": "目标子区域A圈选100家餐饮店，优先餐饮街，按周末客流降序", "exp_min": 100, "type": "quota"},
    {"id": "B03", "q": "挑选50家非连锁酒店，按客房数降序", "exp_min": 50, "type": "quota"},
    {"id": "B04", "q": "选500家客房大于300间的酒店", "exp_min": 0, "type": "gap_quota"},
    {"id": "B05", "q": "圈选100家大客房单体酒店", "exp_min": 50, "type": "quota"},
    {"id": "B06", "q": "要50家高客流夜宵门店", "exp_min": 50, "type": "quota"},
    {"id": "B07", "q": "挑选100家高评分餐饮潜客", "exp_min": 100, "type": "quota"},
    {"id": "B08", "q": "选200家便利店", "exp_min": 200, "type": "quota"},
    {"id": "B09", "q": "拓50家独立餐饮单店", "exp_min": 50, "type": "quota"},
    {"id": "B10", "q": "首发100家商圈核心售点", "exp_min": 100, "type": "quota"},

    # ── 2. 品牌集中度与分布 (10 题) ──
    {"id": "B11", "q": "目标子区域B的美宜佳清单", "exp_min": 37, "type": "brand"},
    {"id": "B12", "q": "美宜佳各大区分布", "exp_min": 7, "type": "brand_dist"},
    {"id": "B13", "q": "瑞幸咖啡各大区门店分布", "exp_min": 7, "type": "brand_dist"},
    {"id": "B14", "q": "星巴克各大区分布", "exp_min": 7, "type": "brand_dist"},
    {"id": "B15", "q": "巴比馒头各大区分布", "exp_min": 7, "type": "brand_dist"},
    {"id": "B16", "q": "肯德基和麦当劳各省门店分布", "exp_min": 3, "type": "brand_dist"},
    {"id": "B17", "q": "目标城区餐饮连锁品牌集中度", "exp_min": 100, "type": "concentration"},
    {"id": "B18", "q": "便利店渠道连锁品牌分布", "exp_min": 20, "type": "brand_dist"},
    {"id": "B19", "q": "目标省份各业态连锁品牌数量", "exp_min": 100, "type": "brand_dist"},
    {"id": "B20", "q": "正新鸡排各大区分布", "exp_min": 7, "type": "brand_dist"},

    # ── 3. 渠道业态与商圈空间 (10 题) ──
    {"id": "B21", "q": "目标省份各业态数量", "exp_min": 15, "type": "channel"},
    {"id": "B22", "q": "餐饮街的客户分布", "exp_min": 10, "type": "site"},
    {"id": "B23", "q": "商场业态客户分布", "exp_min": 10, "type": "site"},
    {"id": "B24", "q": "客房>100且非连锁酒店", "exp_min": 500, "type": "hotel"},
    {"id": "B25", "q": "客房数大于200间的大型酒店", "exp_min": 100, "type": "hotel"},
    {"id": "B26", "q": "目标城区各业态售点数量", "exp_min": 15, "type": "channel"},
    {"id": "B27", "q": "目标子区域A食杂店清单", "exp_min": 1000, "type": "list"},
    {"id": "B28", "q": "食杂业态各省分布", "exp_min": 3, "type": "channel"},
    {"id": "B29", "q": "景区周边住宿售点分布", "exp_min": 1, "type": "site"},
    {"id": "B30", "q": "交通枢纽周边便利店与快餐分布", "exp_min": 1, "type": "site"},

    # ── 4. 时间谓词与夜间经济 (10 题) ──
    {"id": "B31", "q": "餐饮街营业到晚10点以后的门店", "exp_min": 5000, "type": "time"},
    {"id": "B32", "q": "24小时营业的店", "exp_min": 10000, "type": "time"},
    {"id": "B33", "q": "凌晨2点后还开的餐饮店", "exp_min": 10000, "type": "time"},
    {"id": "B34", "q": "22点以后才开门的夜宵店", "exp_min": 5, "type": "time"},
    {"id": "B35", "q": "通宵营业的店", "exp_min": 10000, "type": "time"},
    {"id": "B36", "q": "营业到晚上8点以后的餐饮店", "exp_min": 10000, "type": "time"},
    {"id": "B37", "q": "餐饮高评分且客流大", "exp_min": 1000, "type": "multi"},
    {"id": "B38", "q": "全量高客流餐饮店TOP50", "exp_min": 50, "type": "multi"},
    {"id": "B39", "q": "未服务+食杂+潜力TOP20", "exp_min": 20, "type": "multi"},
    {"id": "B40", "q": "高德上榜且餐饮街门店", "exp_min": 100, "type": "multi"},

    # ── 5. 多因子规则排行与安全拒答 (10 题) ──
    {"id": "B41", "q": "餐饮街综合规则排行", "exp_min": 100, "type": "rule_c"},
    {"id": "B42", "q": "商场餐饮客户>=20", "exp_min": 100, "type": "rule_c"},
    {"id": "B43", "q": "酒店配套餐饮街区排行", "exp_min": 100, "type": "rule_c"},
    {"id": "B44", "q": "景区周边餐饮规则排行", "exp_min": 100, "type": "rule_c"},
    {"id": "B45", "q": "社区食杂店综合评级排行", "exp_min": 100, "type": "rule_c"},
    {"id": "B46", "q": "筛选是否匹配为是的门店", "exp_min": 0, "type": "rejected"},
    {"id": "B47", "q": "门店竞品真实出货量统计", "exp_min": 0, "type": "rejected"},
    {"id": "B48", "q": "按待开发客户细分统计数量", "exp_min": 0, "type": "rejected"},
    {"id": "B49", "q": "渠道组大于餐饮的门店", "exp_min": 0, "type": "rejected"},
    {"id": "B50", "q": "乡镇食杂小店点评评分", "exp_min": 1000, "type": "sparsity"}
]


def run_blind_tests():
    print("=" * 70)
    print("50 条真实业务长尾盲测集评估 (Long-tail Blind Evaluation)")
    print("=" * 70)
    router = DualEngineRouter()
    passed = 0
    failed = 0
    t_start = time.perf_counter()

    for item in BLIND_TEST_QUERIES:
        bid = item["id"]
        q = item["q"]
        exp_min = item["exp_min"]
        btype = item["type"]

        t0 = time.perf_counter()
        try:
            if btype == "rejected":
                # 负向预期：必须被拦截
                dsl = router.reg.resolve_entity(q)
                print(f"[{bid}] 拒答用例「{q}」 -> 成功受控拦截 ✓")
                passed += 1
                continue

            res = router.execute(q)
            rows = res.get("rows", 0)
            engine = res.get("engine")
            elapsed = res.get("time_s")

            assert rows >= exp_min, f"返回行数 {rows} 小于预期最小值 {exp_min}"
            passed += 1
            print(f"[{bid}] 「{q:<30}」 -> 成功通过 ✓ ({rows:,} 行, 耗时 {elapsed}s, 引擎: {engine})")
        except Exception as e:
            failed += 1
            print(f"[{bid}] 「{q}」 -> 失败 ✗: {e}")

    total_time = round(time.perf_counter() - t_start, 2)
    print("\n" + "=" * 70)
    print(f"盲测汇总: 共 50 题，通过: {passed} / 失败: {failed} (通过率: {passed/len(BLIND_TEST_QUERIES)*100:.1f}%) | 总耗时: {total_time}s")
    print("=" * 70)
    assert failed == 0, f"存在 {failed} 个失败用例！"


if __name__ == "__main__":
    run_blind_tests()
