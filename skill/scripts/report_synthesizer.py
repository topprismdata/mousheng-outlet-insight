# -*- coding: utf-8 -*-
"""
report_synthesizer.py
=====================
叙事故事板与 Executive 研报合成器 (Narrative Report Synthesizer v1.0)。
将多步子查询结果、ECharts 可视化图表与数据洞察收束为图文并茂、数据驱动的完整 Executive 分析研报。

主要能力：
1. `generate_executive_report(goal_text)`: 端到端规划 -> 双轨执行 -> 图表绑定 -> 跨章节叙事合成 -> 导出独立 HTML 研报。
2. 产出独立可分享、支持打印/PDF 导出的 HTML 报告页面 (`output/reports/report_*.html`)。
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
REPORTS_DIR = PROJECT_ROOT / "output" / "reports"

from report_planner import AnalyticalGoalPlanner
from chart_adapter import generate_echarts_spec
from dual_router import DualEngineRouter
from adapters.dataframe_adapter import extract_insight_request
from insight import synthesize_from_request


class ExecutiveReportSynthesizer:
    """Executive 分析研报合成引擎。"""

    def __init__(self) -> None:
        self.planner = AnalyticalGoalPlanner()
        self.router = DualEngineRouter()
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def generate_report(self, goal_text: str) -> dict[str, Any]:
        """端到端生成完整分析研报（含数据执行、图表绑定与 HTML 文件输出）。"""
        t_start = time.perf_counter()
        report_id = f"rep-{uuid.uuid4().hex[:10]}"

        # 1. 目标规划分解
        plan = self.planner.plan_report(goal_text)
        sections = plan["sections"]

        executed_sections = []
        key_findings = []

        # 2. 逐章节并发/顺序执行与图表绑定
        for sec in sections:
            sec_id = sec["section_id"]
            sec_title = sec["section_title"]
            query_text = sec["query"]
            chart_type = sec["chart_type"]
            chart_title = sec["chart_title"]

            # 执行查询
            exec_res = self.router.execute(query_text)
            out_df = exec_res["out"]
            dsl = exec_res["meta"].get("dsl", {})
            audit_rep = exec_res.get("audit", {})

            # 合成洞察包
            req = extract_insight_request(dsl, out_df, audit_rep)
            pkg = synthesize_from_request(req, analysis_id=f"{report_id}-{sec_id.lower()}")

            summary = pkg["conclusion"]["summary"]
            verdict = pkg["conclusion"]["verdict"]
            key_findings.append(f"**{sec_title}**：{summary}")

            # 准备图表数据
            preview_records = pkg["evidence"]["table_preview"]
            echarts_spec = generate_echarts_spec(
                chart_type=chart_type,
                records=preview_records,
                title=chart_title
            )

            executed_sections.append({
                "section_id": sec_id,
                "section_title": sec_title,
                "chapter_goal": sec["chapter_goal"],
                "query": query_text,
                "chart_type": chart_type,
                "chart_title": chart_title,
                "rows_count": len(out_df),
                "insight_summary": summary,
                "verdict": verdict,
                "confidence": pkg["conclusion"]["confidence_level"],
                "echarts_spec": echarts_spec,
                "table_preview": preview_records[:10],
                "quota_evaluation": pkg["structure"].get("quota_evaluation"),
                "concentration_metrics": pkg["structure"].get("concentration_metrics")
            })

        # 3. 跨章节宏观执行摘要 (Executive Summary) 提炼
        exec_summary = (
            f"本专题研报针对「{goal_text}」进行了 4 维度深度数据挖掘，覆盖 <n_rows> 个目标区域在册售点。"
            f"分析发现：{ '；'.join(s['verdict'] for s in executed_sections[:3]) }。"
        )

        total_elapsed = round(time.perf_counter() - t_start, 2)

        report_payload = {
            "report_id": report_id,
            "report_title": plan["report_title"],
            "executive_theme": plan["executive_theme"],
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "target_industry": plan["target_industry"],
            "target_region": plan["target_region"],
            "executive_summary": exec_summary,
            "key_findings": key_findings,
            "total_sections": len(executed_sections),
            "total_elapsed_seconds": total_elapsed,
            "sections": executed_sections,
            "limitations": [
                "本研报结论基于当前在册售点静态数据与统计模型，不构成排他性商业投资决策",
                "部分下沉小店缺少大众点评或携程登记，实际分析中已按置信度规范防御性声明限制"
            ],
            "recommended_next_steps": [
                {"question": "对比各入围商圈内部的便利零售供给缺口", "intent": "渠道空白挖掘"},
                {"question": "进一步叠加营业时间与客流阈值，观察夜经济脉冲特征", "intent": "时空潮汐下钻"}
            ]
        }

        # 4. 导出独立 HTML 报告
        html_file_path = REPORTS_DIR / f"{report_id}.html"
        self._export_html_report(report_payload, html_file_path)
        report_payload["html_report_path"] = str(html_file_path)

        return report_payload

    def _export_html_report(self, report: dict[str, Any], output_path: Path) -> None:
        """生成交互式 HTML 研报。"""
        sections_html = ""
        charts_js = ""

        for idx, sec in enumerate(report["sections"]):
            chart_dom_id = f"chart_{sec['section_id']}"
            spec_json = json.dumps(sec["echarts_spec"], ensure_ascii=False)

            # 表格预览行
            table_rows = ""
            if sec["table_preview"]:
                cols = list(sec["table_preview"][0].keys())
                thead = "".join(f"<th>{c}</th>" for c in cols)
                rows_list = []
                for row in sec["table_preview"][:5]:
                    tds = "".join(f"<td>{row.get(c, '-')}</td>" for c in cols)
                    rows_list.append(f"<tr>{tds}</tr>")
                tbody = "".join(rows_list)
                table_rows = f"""
                <div class="table-wrap">
                  <table>
                    <thead><tr>{thead}</tr></thead>
                    <tbody>{tbody}</tbody>
                  </table>
                </div>
                """

            # 集中度/配额徽章
            badge_html = ""
            cm = sec.get("concentration_metrics")
            if cm and cm.get("hhi", 0) > 0:
                badge_html = f'<div class="badge">集中度建模: HHI={cm.get("hhi")} | CR4={(cm.get("cr4",0)*100):.1f}% | 评级: {cm.get("category")}</div>'

            qe = sec.get("quota_evaluation")
            if qe:
                badge_html += f'<div class="badge quota">目标配额: {qe.get("target_quota")}家 | 达成状态: {qe.get("fulfillment_status")} | 候选池: {qe.get("pool_size"):,}家</div>'

            sections_html += f"""
            <section class="report-section">
              <h2>{sec['section_title']}</h2>
              <p class="chapter-goal">🎯 分析目标：{sec['chapter_goal']}</p>
              
              <div class="insight-box">
                <strong>💡 核心洞察：</strong>{sec['insight_summary']}
                <div class="verdict">定性判断：{sec['verdict']}</div>
                {badge_html}
              </div>

              <div class="chart-container" id="{chart_dom_id}"></div>
              {table_rows}
            </section>
            """

            charts_js += f"""
            (function() {{
              var chartDom = document.getElementById('{chart_dom_id}');
              if (chartDom) {{
                var myChart = echarts.init(chartDom);
                var option = {spec_json};
                myChart.setOption(option);
                window.addEventListener('resize', function() {{ myChart.resize(); }});
              }}
            }})();
            """

        limitations_html = "".join(f"<div>· {lim}</div>" for lim in report["limitations"])
        next_steps_html = "".join(f"<div>· {nxt['question']}（{nxt['intent']}）</div>" for nxt in report["recommended_next_steps"])

        html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{report['report_title']}</title>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.6.0/dist/echarts.min.js"></script>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Noto Sans SC", "PingFang SC", sans-serif; background: #f6f7f4; color: #17201d; margin: 0; padding: 32px 16px; }}
    .container {{ max-width: 960px; margin: 0 auto; background: #fff; border: 1px solid #d9ddd7; border-radius: 8px; padding: 40px; box-shadow: 0 8px 32px rgba(23,32,29,.06); }}
    .header {{ border-bottom: 2px solid #ad3324; padding-bottom: 20px; margin-bottom: 24px; }}
    h1 {{ font-family: "Noto Serif SC", serif; font-size: 24px; color: #17201d; margin: 0 0 10px; }}
    .meta {{ font-size: 11px; color: #697871; display: flex; gap: 18px; flex-wrap: wrap; }}
    .executive-card {{ background: #fbfbf9; border-left: 4px solid #ad3324; border-radius: 0 6px 6px 0; padding: 16px 20px; margin: 24px 0 32px; }}
    .executive-card h3 {{ margin: 0 0 8px; font-size: 14px; color: #ad3324; }}
    .executive-card p {{ font-size: 13px; line-height: 1.7; margin: 0; color: #2c3833; }}
    .report-section {{ margin-bottom: 40px; padding-bottom: 32px; border-bottom: 1px dashed #d9ddd7; }}
    h2 {{ font-family: "Noto Serif SC", serif; font-size: 18px; margin: 0 0 8px; color: #17201d; }}
    .chapter-goal {{ font-size: 11px; color: #798a82; margin: 0 0 14px; }}
    .insight-box {{ background: #f4f7f4; border: 1px solid #c9d8ce; border-radius: 6px; padding: 14px; margin-bottom: 16px; font-size: 12px; line-height: 1.6; }}
    .verdict {{ background: #e3ede6; color: #1d4d3e; padding: 4px 8px; border-radius: 4px; display: inline-block; margin-top: 8px; font-weight: 500; }}
    .badge {{ display: inline-block; background: #e8f0ec; color: #235c4b; font-size: 10px; padding: 3px 8px; border-radius: 4px; margin: 6px 6px 0 0; font-family: monospace; }}
    .badge.quota {{ background: #fcedea; color: #ad3324; }}
    .chart-container {{ width: 100%; height: 280px; margin: 16px 0; }}
    .table-wrap {{ overflow-x: auto; margin-top: 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 10.5px; }}
    th, td {{ padding: 6px 10px; border-bottom: 1px solid #eaeeea; text-align: left; }}
    th {{ background: #f0f3ef; color: #55665e; }}
    .footer-box {{ background: #fafaf8; border: 1px solid #d9ddd7; border-radius: 6px; padding: 16px; margin-top: 24px; font-size: 11px; line-height: 1.6; }}
    .footer-box strong {{ display: block; margin-bottom: 4px; color: #17201d; }}
    .btn-print {{ float: right; background: #235c4b; color: #fff; border: 0; padding: 6px 12px; border-radius: 4px; font-size: 11px; cursor: pointer; }}
  </style>
</head>
<body>
  <div class="container">
    <button class="btn-print" onclick="window.print()">🖨️ 导出 / 打印研报</button>
    <div class="header">
      <h1>{report['report_title']}</h1>
      <div class="meta">
        <span>🕒 生成时间: {report['generated_at']}</span>
        <span>🏷️ 行业主题: {report['target_industry']}</span>
        <span>📍 覆盖范围: {report['target_region']}</span>
        <span>⚡ 分析耗时: {report['total_elapsed_seconds']}s</span>
      </div>
    </div>

    <div class="executive-card">
      <h3>📋 核心执行摘要 (Executive Summary)</h3>
      <p>{report['executive_summary']}</p>
    </div>

    {sections_html}

    <div class="footer-box">
      <strong>⚠️ 数据质量与适用边界声明 (Limitations)</strong>
      {limitations_html}
      <strong style="margin-top:10px;">🔍 建议下一步继续分析方向 (Next Steps)</strong>
      {next_steps_html}
    </div>
  </div>

  <script>
    {charts_js}
  </script>
</body>
</html>
"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_doc)


if __name__ == "__main__":
    synthesizer = ExecutiveReportSynthesizer()
    print("=== 智能多维研报合成器测试 ===")
    rep = synthesizer.generate_report("请生成目标城市现磨咖啡竞争格局与未服务潜客开发报告")
    print(f"\n[研报生成成功] {rep['report_title']}")
    print(f"包含章节数: {rep['total_sections']} 个 | 总耗时: {rep['total_elapsed_seconds']}s")
    print(f"HTML 研报输出路径: {rep['html_report_path']}")
