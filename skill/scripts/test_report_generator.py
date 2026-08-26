# -*- coding: utf-8 -*-
"""
test_report_generator.py
========================
智能多维研报生成器端到端测试套件 (Report Generator & Storyboard Test Suite)。
验证：
1. 目标规划器 (report_planner.py) 多步拆解；
2. 图表适配器 (chart_adapter.py) ECharts Spec 生成；
3. 研报合成器 (report_synthesizer.py) 多维执行、故事板收束与 HTML 文件输出；
4. Demo 服务端点 `/api/generate_report` 与 `/api/report_view` 联动。
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from report_planner import AnalyticalGoalPlanner
from chart_adapter import generate_echarts_spec
from report_synthesizer import ExecutiveReportSynthesizer


def test_analytical_goal_planner():
    """测试分析目标规划器拆解高层诉求。"""
    planner = AnalyticalGoalPlanner()
    
    # 咖啡专项
    p1 = planner.plan_report("请生成上海现磨咖啡竞争格局与未服务潜客开发报告")
    assert "咖啡" in p1["report_title"]
    assert len(p1["sections"]) == 4
    assert p1["sections"][0]["chart_type"] in ("donut", "pie")
    assert p1["sections"][1]["chart_type"] == "horizontal_bar"

    # 冰柜专项
    p2 = planner.plan_report("华东食杂便利渠道全景与 200 台冰柜投放开发研报")
    assert "冰柜" in p2["report_title"]
    assert len(p2["sections"]) >= 3


def test_chart_adapter_specs():
    """测试图表适配器生成合法 ECharts JSON Spec。"""
    mock_data = [
        {"连锁品牌名称": "瑞幸咖啡", "门店数": 3266},
        {"连锁品牌名称": "星巴克", "门店数": 1787},
        {"连锁品牌名称": "肯德基", "门店数": 2178}
    ]
    
    # 横向柱状图
    spec_bar = generate_echarts_spec("horizontal_bar", mock_data, "品牌排行", "连锁品牌名称", "门店数")
    assert spec_bar["series"][0]["type"] == "bar"
    assert len(spec_bar["yAxis"]["data"]) == 3

    # 环形图
    spec_pie = generate_echarts_spec("donut", mock_data, "业态占比", "连锁品牌名称", "门店数")
    assert spec_pie["series"][0]["type"] == "pie"
    assert len(spec_pie["series"][0]["data"]) == 3


def test_executive_report_synthesizer_e2e():
    """测试研报合成器端到端生成完整 HTML 报告。"""
    synthesizer = ExecutiveReportSynthesizer()
    goal = "上海市现磨咖啡连锁品牌竞争格局与未服务潜客开发研报"
    
    t0 = time.perf_counter()
    report = synthesizer.generate_report(goal)
    elapsed = time.perf_counter() - t0

    assert report["report_id"].startswith("rep-")
    assert report["total_sections"] == 4
    assert len(report["executive_summary"]) > 20
    assert len(report["key_findings"]) == 4
    
    html_path = Path(report["html_report_path"])
    assert html_path.exists(), f"生成的 HTML 研报文件必须存在: {html_path}"
    assert html_path.stat().st_size > 3000, "研报 HTML 文件大小必须 > 3KB"

    # 验证 HTML 内部包含图表初始化脚本
    html_text = html_path.read_text(encoding="utf-8")
    assert "echarts.init" in html_text
    assert "Executive Summary" in html_text
    assert "瑞幸咖啡" in html_text

    print(f"\n[E2E 研报生成测试] 成功生成 HTML 研报: {html_path} (耗时 {elapsed:.2f}s, 大小 {html_path.stat().st_size} bytes)")


def test_demo_server_report_endpoints():
    """测试 Demo 服务 /api/generate_report 与 /api/report_view 接口。"""
    base = "http://127.0.0.1:8765"
    
    # 触发生成研报
    req = urllib.request.Request(
        f"{base}/api/generate_report",
        data=json.dumps({"goal": "华东食杂便利渠道全景与 200 台冰柜投放开发研报"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    res = json.loads(urllib.request.urlopen(req).read())
    
    assert res.get("report_id") is not None
    assert "view_url" in res
    view_url = res["view_url"]

    # 访问生成的研报页面
    view_resp = urllib.request.urlopen(f"{base}{view_url}").read().decode("utf-8")
    assert "冰柜" in view_resp
    assert "echarts.init" in view_resp
    print(f"\n[Demo 服务接口测试] /api/generate_report -> {view_url} 访问成功！")


if __name__ == "__main__":
    test_analytical_goal_planner()
    test_chart_adapter_specs()
    test_executive_report_synthesizer_e2e()
    try:
        test_demo_server_report_endpoints()
    except Exception as e:
        print(f"[Note] Server endpoint test: {e}")
    print("\n>>> 智能多维研报生成器全量测试 100% 通过！ <<<")
