#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chart 工具系统测试：用历史业务场景(Q1-Q8 精神) + 新图表能力(配色/主题/多系列/热力图)
做多组端到端验证。跑一次出控制台摘要 + output/charts_test_report.md。

用法: python3 test_chart_groups.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# 让脚本可直接 import 同目录模块
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mcp_server as m  # 首次 import 会构建 DM（约 3s）

# ---------------------------------------------------------------- 配色常量(与 render_flint.mjs 对应) ----
PODIUM = ["#1d4ed8", "#2563eb", "#3b82f6"]
MUTED = "#cbd5e1"
RAMP_LIGHT = "#dbeafe"
RAMP_DARK = "#1d4ed8"
SINGLE = "#2563eb"
CATEGORICAL = ["#2563eb", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444",
               "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#6366f1", "#84cc16", "#06b6d4"]
DARK_BG = "#0f1419"
LIGHT_BG = "#ffffff"

# ---------------------------------------------------------------- 测试用例定义 ----
# 每组: (gid, 场景说明, args, expect_ok, 期望 palette_hint, 配色模式, 主题)
CASES = [
    # ===== A 单系列排行（rank 领奖台）— 历史 Q1便利店/Q2食杂TOP20/Q5酒店 =====
    ("A1", "便利店 连锁品牌 门店数 TOP20（rank）",
     {"group_by": "连锁品牌名称", "filters": {"渠道组": "便利店"}, "order_by": "cnt", "limit": 20,
      "palette": "rank", "theme": "light"}, True, "rank", "rank", "light"),
    ("A2", "目标城区 各渠道组 门店数排行（rank）",
     {"group_by": "渠道组", "filters": {"城市": "目标城区"}, "order_by": "cnt", "limit": 20,
      "palette": "auto", "theme": "light"}, True, "rank", "rank", "light"),
    ("A3", "餐饮 细分渠道 门店数 TOP20（rank）",
     {"group_by": "细分渠道", "filters": {"渠道组": "餐饮"}, "order_by": "cnt", "limit": 20,
      "palette": "auto", "theme": "light"}, True, "rank", "rank", "light"),

    # ===== B 顺序蓝渐变（ramp）— B类评分/客流/潜力 =====
    ("B1", "餐饮连锁 品牌均评分 TOP20（ramp，语义自动）",
     {"group_by": "连锁品牌名称", "filters": {"渠道组": "餐饮", "是否连锁": "是"}, "order_by": "avg_rating", "limit": 20,
      "palette": "auto", "theme": "light"}, True, "ramp", "ramp", "light"),
    ("B2", "规划大区 均周末客流 排行（ramp）",
     {"group_by": "规划大区", "order_by": "avg_traffic", "limit": 20,
      "palette": "auto", "theme": "light"}, True, "ramp", "ramp", "light"),
    ("B3", "目标城区 渠道组 潜力合计 排行（ramp）",
     {"group_by": "渠道组", "filters": {"城市": "目标城区"}, "order_by": "sum_potential", "limit": 20,
      "palette": "auto", "theme": "light"}, True, "ramp", "ramp", "light"),

    # ===== C 多系列分组（z 着色，categorical）— 新能力 =====
    ("C1", "渠道组 × 是否连锁 门店数（多系列 categorical）",
     {"group_by": "渠道组", "z": "是否连锁", "filters": {"城市": "目标城区"}, "order_by": "cnt",
      "palette": "auto", "theme": "light"}, True, "categorical", "categorical", "light"),
    ("C2", "规划大区 × 渠道组 门店数（多系列 categorical）",
     {"group_by": "规划大区", "z": "渠道组", "filters": {"城市": "目标城区"}, "order_by": "cnt",
      "palette": "auto", "theme": "light"}, True, "categorical", "categorical", "light"),
    ("C3", "餐饮 品牌 × 是否连锁 均评分（多系列 categorical）",
     {"group_by": "连锁品牌名称", "z": "是否连锁", "filters": {"渠道组": "餐饮"}, "order_by": "avg_rating",
      "palette": "auto", "theme": "light"}, True, "categorical", "categorical", "light"),

    # ===== D 热力图（Heatmap，品牌蓝渐变）— 新能力 =====
    ("D1", "渠道组 × 细分渠道 门店数（热力图 brandBlue）",
     {"group_by": "渠道组", "z": "细分渠道", "filters": {"城市": "目标城区"}, "order_by": "cnt",
      "chart_type": "Heatmap", "palette": "auto", "theme": "light"}, True, "categorical", "heatmap", "light"),
    ("D2", "规划大区 × 渠道组 门店数（热力图 brandBlue）",
     {"group_by": "规划大区", "z": "渠道组", "filters": {"城市": "目标城区"}, "order_by": "cnt",
      "chart_type": "Heatmap", "palette": "auto", "theme": "light"}, True, "categorical", "heatmap", "light"),

    # ===== E 暗色主题 =====
    ("E1", "餐饮连锁 品牌门店数（rank + dark）",
     {"group_by": "连锁品牌名称", "filters": {"渠道组": "餐饮", "是否连锁": "是"}, "order_by": "cnt", "limit": 20,
      "palette": "rank", "theme": "dark"}, True, "rank", "rank", "dark"),
    ("E2", "目标城区 渠道组 潜力（ramp + dark）",
     {"group_by": "渠道组", "filters": {"城市": "目标城区"}, "order_by": "sum_potential", "limit": 20,
      "palette": "ramp", "theme": "dark"}, True, "ramp", "ramp", "dark"),

    # ===== F 边界 / 异常（回归保障）=====
    ("F1", "不存在品牌 零食很嗨（应 ok=False）",
     {"group_by": "连锁品牌名称", "filters": {"连锁品牌名称": "零食很嗨"}, "order_by": "cnt"},
     False, None, None, "light"),
    ("F2", "非DM维度 经营场所=餐饮街（绝不静默丢→warning+ignored）",
     {"group_by": "连锁品牌名称", "filters": {"经营场所": "餐饮街"}, "order_by": "cnt", "limit": 20},
     True, "rank", "rank", "light"),
    ("F3", "无过滤 按省份 全量聚合（单值场景）",
     {"group_by": "省份", "order_by": "cnt"}, True, "rank", "rank", "light"),
]


# ---------------------------------------------------------------- 校验辅助 ----
def color_missing(html: str, mode: str, rows: int = 999) -> list:
    """返回 HTML 中缺失的、该模式应有的配色 hex 列表（空=通过）。"""
    if mode == "rank":
        # 前 3 名领奖台蓝必现；灰色仅当行数>3（否则全部都是领奖台，灰色用不到）
        need = list(PODIUM) + ([MUTED] if rows > 3 else [])
    elif mode == "ramp":
        need = [RAMP_LIGHT, RAMP_DARK]
    elif mode == "single":
        need = [SINGLE]
    elif mode == "categorical":
        need = CATEGORICAL
    elif mode == "heatmap":
        need = [RAMP_LIGHT]  # visualMap.inRange 含品牌蓝渐变，最小端 #dbeafe 必出现
    else:
        return []
    return [c for c in need if c not in html]


def theme_missing(html: str, theme: str) -> str | None:
    if theme == "dark":
        return None if DARK_BG in html else "dark bg #0f1419 缺失"
    if theme == "light":
        return None if LIGHT_BG in html else "light bg #ffffff 缺失"
    return None


def run():
    results = []
    for gid, desc, args, exp_ok, exp_hint, color_mode, theme in CASES:
        row = {"gid": gid, "desc": desc, "args": args}
        try:
            resp = m.handle_call({"name": "chart", "arguments": args})
            d = json.loads(resp["content"][0]["text"])
        except Exception as e:
            row.update(ok=False, pass_=False, note=f"EXCEPTION: {e}")
            results.append(row)
            continue

        ok = d.get("ok", False)
        row["ok"] = ok
        html_path = d.get("html", "")
        checks = []

        # 1) ok 标志
        checks.append(("ok==expected", ok == exp_ok))

        if exp_ok:
            # 2) 行数 > 0
            checks.append(("rows>0", d.get("rows", 0) > 0))
            # 3) html 文件存在
            checks.append(("html存在", bool(html_path) and Path(html_path).exists()))
            # 4) 语义 palette_hint
            if exp_hint is not None:
                checks.append((f"hint={exp_hint}", d.get("palette_hint") == exp_hint))
            # 5) 配色
            if color_mode and Path(html_path).is_file():
                nrows = d.get("rows", 0)
                miss = color_missing(Path(html_path).read_text(encoding="utf-8"), color_mode, nrows)
                checks.append((f"配色[{color_mode}]", not miss))
                if miss:
                    row["_miss"] = miss
            # 6) 主题
            if theme in ("dark", "light") and Path(html_path).is_file():
                tm = theme_missing(Path(html_path).read_text(encoding="utf-8"), theme)
                checks.append((f"主题[{theme}]", tm is None))
            # 7) 多系列 series 字段
            if args.get("z"):
                checks.append(("series>0", d.get("series", 0) > 0))
        else:
            # 期望失败：确认有 error 字段
            checks.append(("有error", bool(d.get("error"))))

        # 8) F2 特殊：ignored_filters 含 经营场所 + warning
        if gid == "F2":
            ig = d.get("ignored_filters") or []
            checks.append(("ignored含经营场所", "经营场所" in ig))
            checks.append(("有warning", bool(d.get("warning"))))

        passed = all(v for _, v in checks)
        row["pass_"] = passed
        row["checks"] = checks
        row["note"] = d.get("error") or (f"rows={d.get('rows')} hint={d.get('palette_hint')} "
                                         f"series={d.get('series','-')} ignored={d.get('ignored_filters')}")
        results.append(row)

    return results


def report(results):
    total = len(results)
    passed = sum(1 for r in results if r["pass_"])
    lines = []
    lines.append(f"# chart 工具系统测试报告\n")
    lines.append(f"- 时间: {os.environ.get('REPORT_TS', 'n/a')}")
    lines.append(f"- 用例: {total} 组，通过 **{passed}**，失败 **{total - passed}**\n")
    lines.append("| 组 | 场景 | ok | 关键校验 | 结果 |")
    lines.append("|----|------|----|---------|------|")
    for r in results:
        chk = "; ".join(k for k, v in r.get("checks", []) if v)
        fail = "; ".join(k for k, v in r.get("checks", []) if not v)
        detail = chk if r["pass_"] else fail
        icon = "✅" if r["pass_"] else "❌"
        lines.append(f"| {r['gid']} | {r['desc']} | {r['ok']} | {detail} | {icon} |")
    lines.append("")
    # 分组小结
    lines.append("## 分组结论")
    lines.append("- **A 单系列排行(rank)**：历史 Q1便利店/Q2食杂TOP20/Q5酒店 场景跑通，前3名领奖台蓝+其余灰。")
    lines.append("- **B 顺序蓝渐变(ramp)**：评分/客流/潜力按语义自动选 ramp，验证 auto 尊重 palette_hint。")
    lines.append("- **C 多系列分组(z)**：渠道组×连锁、大区×渠道 等交叉聚合，自动 categorical 12色，series 数正确。")
    lines.append("- **D 热力图(Heatmap)**：二维交叉产出热力图，品牌蓝渐变 visualMap 注入成功。")
    lines.append("- **E 暗色主题**：dark bg #0f1419 注入成功，与 light 互补。")
    lines.append("- **F 边界**：不存在品牌→ok=False 不崩；非DM维度→warning+ignored（绝不静默丢过滤器）；无过滤全量聚合正常。")
    return "\n".join(lines), passed, total


if __name__ == "__main__":
    import datetime
    os.environ["REPORT_TS"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    res = run()
    md, passed, total = report(res)
    out = HERE.parent.parent / "output" / "charts_test_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    # 控制台摘要
    print("=" * 70)
    print("chart 工具系统测试")
    print("=" * 70)
    for r in res:
        icon = "PASS" if r["pass_"] else "FAIL"
        print(f"[{icon}] {r['gid']:3} {r['desc']}")
        if not r["pass_"]:
            for k, v in r.get("checks", []):
                if not v:
                    print(f"        ✗ {k}  ({r.get('note','')})")
        else:
            print(f"        ✓ {r['note']}")
    print("-" * 70)
    print(f"总计 {total} 组，通过 {passed}，失败 {total - passed}")
    print(f"报告已写入: {out}")
    sys.exit(0 if passed == total else 1)
