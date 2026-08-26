"""test_chart_types_v2.py — 验证新增图表类型（Pie/Donut/Area/Line/Combo）

走真实 MCP chart 工具路径：query_stat → build_flint_spec → render_flint.mjs，
检查生成的 HTML 内含预期的 ECharts 结构 + 配色/主题生效。

运行: python test_chart_types_v2.py
"""
import json
import sys
from pathlib import Path

import mcp_server as m

V3 = Path("/Users/ghb/outlet-insight/output/charts_v3")
V3.mkdir(parents=True, exist_ok=True)

DB_READY = getattr(m.SESS, "dm_db", None) is not None
if not DB_READY:
    print("[WARN] DM 未就绪，先确保 SESS 已 ensure_dm()")

PASS, FAIL = 0, 0
REPORT = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        REPORT.append(f"  ✅ {name} {detail}")
    else:
        FAIL += 1
        REPORT.append(f"  ❌ {name} {detail}")


def call_chart(tag, **a):
    """tag 用于隔离输出文件，避免同参数不同图表类型互相覆盖。"""
    a = dict(a)
    a["out_html"] = str(V3 / f"{tag}.html")
    d = json.loads(m.handle_call({"name": "chart", "arguments": a})["content"][0]["text"])
    return d


def has(htm, s):
    return s in htm


def load_option(htm):
    """精确提取 mountScript 里的 setOption(JSON) 并解析，避免匹配到 echarts 库代码。"""
    import re
    m = re.search(r"getElementById\('chart'\)\)\.setOption\((.*)\);", htm, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def series_types(opt):
    return [s.get("type") for s in (opt or {}).get("series", [])]


def series_has_area(opt):
    return any("areaStyle" in s for s in (opt or {}).get("series", []))


# ------------------------------------------------------------------
# G1 饼图 Pie Chart（门店数占比）
# ------------------------------------------------------------------
print("== G1 饼图 Pie ==")
d = call_chart("G1_pie", group_by="渠道组", filters={"城市": "目标城区"}, y="cnt", chart_type="Pie Chart")
check("G1 ok", d.get("ok"), str(d.get("error", "")))
htm = Path(d["html"]).read_text(encoding="utf-8") if d.get("ok") else ""
opt = load_option(htm)
check("G1 解析 option", opt is not None, "")
check("G1 含 pie series", "pie" in series_types(opt), str(series_types(opt)))
check("G1 含 radius(普通饼)", '"radius"' in htm, "")
check("G1 非环形", '["42%","70%"]' not in htm, "")
check("G1 扇区分类色板", any(c in htm for c in ["#2563eb", "#0ea5e9", "#10b981"]), "")
REPORT.append(f"     html={d.get('html')}")

# ------------------------------------------------------------------
# G2 环形图 Donut Chart
# ------------------------------------------------------------------
print("== G2 环形 Donut ==")
d = call_chart("G2_donut", group_by="渠道组", filters={"城市": "目标城区"}, y="cnt", chart_type="Donut Chart")
check("G2 ok", d.get("ok"), str(d.get("error", "")))
htm = Path(d["html"]).read_text(encoding="utf-8") if d.get("ok") else ""
opt = load_option(htm)
check("G2 含 pie series", "pie" in series_types(opt), str(series_types(opt)))
check("G2 环形 radius", '["42%","70%"]' in htm, "")
REPORT.append(f"     html={d.get('html')}")

# ------------------------------------------------------------------
# G3 面积图 Area Chart（均评分趋势）
# ------------------------------------------------------------------
print("== G3 面积 Area ==")
d = call_chart("G3_area", group_by="规划大区", y="avg_rating", chart_type="Area Chart", limit=10)
check("G3 ok", d.get("ok"), str(d.get("error", "")))
htm = Path(d["html"]).read_text(encoding="utf-8") if d.get("ok") else ""
opt = load_option(htm)
check("G3 含 line", "line" in series_types(opt), str(series_types(opt)))
check("G3 含 areaStyle(面积填充)", series_has_area(opt), "")
check("G3 非饼/环", "pie" not in series_types(opt), "")
REPORT.append(f"     html={d.get('html')}")

# ------------------------------------------------------------------
# G4 折线图 Line Chart（门店数趋势）
# ------------------------------------------------------------------
print("== G4 折线 Line ==")
d = call_chart("G4_line", group_by="渠道组", filters={"城市": "目标城区"}, y="cnt", chart_type="Line Chart")
check("G4 ok", d.get("ok"), str(d.get("error", "")))
htm = Path(d["html"]).read_text(encoding="utf-8") if d.get("ok") else ""
opt = load_option(htm)
check("G4 含 line", "line" in series_types(opt), str(series_types(opt)))
check("G4 无面积填充(纯折线)", not series_has_area(opt), "")
REPORT.append(f"     html={d.get('html')}")

# ------------------------------------------------------------------
# G5 组合图 Combo（柱 门店数 + 线 均评分，双轴）
# ------------------------------------------------------------------
print("== G5 组合 Combo ==")
d = call_chart("G5_combo", group_by="连锁品牌名称",
               filters={"渠道组": "餐饮", "是否连锁": "是"},
               y="cnt", y2="avg_rating", limit=15)
check("G5 ok", d.get("ok"), str(d.get("error", "")))
check("G5 summary.y2", d.get("y2") == "avg_rating", str(d.get("y2")))
check("G5 summary.combo", d.get("combo") is True, "")
htm = Path(d["html"]).read_text(encoding="utf-8") if d.get("ok") else ""
opt = load_option(htm)
st = series_types(opt)
check("G5 双轴 yAxisIndex:1", any(s.get("yAxisIndex") == 1 for s in (opt or {}).get("series", [])), "")
check("G5 含柱 series", "bar" in st, str(st))
check("G5 含线 series", "line" in st, str(st))
check("G5 柱品牌蓝", any(s.get("type") == "bar" and "2563eb" in json.dumps(s) for s in (opt or {}).get("series", [])), "")
check("G5 线橙色", any(s.get("type") == "line" and "f59e0b" in json.dumps(s) for s in (opt or {}).get("series", [])), "")
REPORT.append(f"     html={d.get('html')}")

# ------------------------------------------------------------------
# G6 组合图异常：y2 与 y 相同 → 应报错
# ------------------------------------------------------------------
print("== G6 组合异常校验 ==")
d = call_chart("G6_err", group_by="渠道组", filters={"城市": "目标城区"}, y="cnt", y2="cnt")
check("G6 应失败", not d.get("ok"), "")
check("G6 报错含提示", "相同" in str(d.get("error", "")), str(d.get("error", "")))

# ------------------------------------------------------------------
# G7 暗色主题 + 饼图
# ------------------------------------------------------------------
print("== G7 暗色主题 ==")
d = call_chart("G7_dark", group_by="渠道组", filters={"城市": "目标城区"}, y="cnt",
               chart_type="Pie Chart", theme="dark")
check("G7 ok", d.get("ok"), str(d.get("error", "")))
htm = Path(d["html"]).read_text(encoding="utf-8") if d.get("ok") else ""
check("G7 暗色背景", has(htm, "#0f1419"), "")
REPORT.append(f"     html={d.get('html')}")

# ------------------------------------------------------------------
# G8 验证 _safe_name 改进：同参数不同 chart_type 不再覆盖同一文件
# ------------------------------------------------------------------
print("== G8 safe_name 隔离 ==")
import re
n1 = m._safe_name({"group_by": "渠道组", "filters": {"城市": "目标城区"},
                   "order_by": "cnt", "chart_type": "Pie Chart"})
n2 = m._safe_name({"group_by": "渠道组", "filters": {"城市": "目标城区"},
                   "order_by": "cnt", "chart_type": "Line Chart"})
check("G8 不同 chart_type 文件名不同", n1 != n2, f"{n1} vs {n2}")


print("\n".join(REPORT))
print(f"\n总计: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
