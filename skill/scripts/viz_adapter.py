"""viz_adapter.py — sh-store-insight × Flint Chart 适配层（Python 侧）

职责：把咱们的查询/统计结果（pandas DataFrame）转成 Flint 的
`ChartAssemblyInput` JSON —— 即 data + semantic_types + chart_spec。
Flint 负责后续"数据 → 原生图表"的编译（见 render_flint.mjs）。

设计原则（对齐 sh-store-insight 铁律）：
- 绝不静默丢字段：列若无法推断语义，显式回退到 Category / Quantity 而非丢弃。
- 零 LLM：语义类型与图表类型靠规则 + 已知 schema 推导，不调模型。
- 确定性：同名同数据 → 同 spec。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Optional

import pandas as pd

# --------------------------------------------------------------------------
# 1. 字段名 → Flint 语义类型 的启发式映射
#    Flint 支持的语义类型（节选，覆盖咱们常用）：
#    Quantity / Count / Amount / Price / Percentage / Score / Rank
#    Category / Name / Status / City / Region / Country ...
# --------------------------------------------------------------------------
_SEMANTIC_HINTS: list[tuple[list[str], str]] = [
    (["评分", "分", "rating", "score"], "Score"),
    (["占比", "率", "percent", "比例"], "Percentage"),
    (["门店数", "数量", "客流", "潜力", "规模", "数", "量",
      "count", "amount", "price", "收入"], "Quantity"),
    (["品牌", "名称", "渠道", "业态", "类型", "状态", "场所"], "Category"),
    (["城市", "city"], "City"),
    (["省份", "省", "地区", "区域", "region"], "Region"),
    (["国家", "country"], "Country"),
]


def infer_semantic(col: str, series: pd.Series) -> str:
    """按列名启发式 + dtype 兜底推断 Flint 语义类型。"""
    low = col.lower()
    for keys, stype in _SEMANTIC_HINTS:
        if any(k in low for k in keys):
            return stype
    if pd.api.types.is_numeric_dtype(series):
        return "Quantity"
    # 低基数字符串当作类别，否则当 Name（高基数离散标识）
    nunique = series.nunique(dropna=True)
    return "Category" if nunique <= 50 else "Name"


# --------------------------------------------------------------------------
# 2. 图表类型推导
# --------------------------------------------------------------------------
# 时间/有序维度名线索 → 倾向折线（看趋势而非离散排名）
_TEMPORAL_HINTS = ["日期", "时间", "月", "月份", "季度", "年", "week", "month",
                  "quarter", "year", "date", "time", "day"]


def _is_temporal(col: str, series: pd.Series) -> bool:
    low = col.lower()
    if any(k in low for k in _TEMPORAL_HINTS):
        return True
    # pandas 时间类型
    try:
        if str(series.dtype).startswith("datetime"):
            return True
    except Exception:
        pass
    # 值识别：x 的取值大部分像 年份(19xx/20xx)/月份/日期 → 趋势折线更合适
    vals = series.dropna().astype(str)
    if len(vals) == 0:
        return False
    year_pat = re.compile(r"^(?:19|20)\d{2}$")
    n_year = int(vals.str.match(year_pat).sum())
    if n_year / len(vals) >= 0.7:
        return True
    # 兼容 "YYYY-MM" / "YYYY/MM" / 完整日期
    ym_pat = re.compile(r"^(?:19|20)\d{2}[-/]\d{1,2}$")
    if int(vals.str.match(ym_pat).sum()) / len(vals) >= 0.7:
        return True
    return False


def infer_chart_type(df: pd.DataFrame, x: str, y: Optional[str],
                     z: Optional[str] = None) -> str:
    """根据可用轴推导默认图表类型。

    优先级：
    - x + y + z → Heatmap（两维一量 → 热力图）
    - x 时间/有序 + y 数值 → Line Chart（看趋势）
    - x 类别 + y 数值 → Bar Chart（最契合咱们的 DM 排行场景）
    - x、y 均数值 → Scatter Plot（看关系/分布）
    - 仅 x → Bar Chart
    """
    if x and y and z:
        return "Heatmap"
    if x and y:
        xt = infer_semantic(x, df[x]) if x in df.columns else "Category"
        temporal = _is_temporal(x, df[x]) if x in df.columns else False
        if temporal:
            return "Line Chart"
        if xt in ("City", "Region", "Country", "Category", "Name", "Status"):
            return "Bar Chart"
        return "Scatter Plot"
    if x and not y:
        return "Bar Chart"
    return "Bar Chart"


# --------------------------------------------------------------------------
# 3. 核心：DataFrame → Flint spec
# --------------------------------------------------------------------------
def build_flint_spec(df: pd.DataFrame,
                     x: Optional[str] = None,
                     y: Optional[str] = None,
                     z: Optional[str] = None,
                     y2: Optional[str] = None,
                     chart_type: Optional[str] = None,
                     title: str = "",
                     base_size: tuple[int, int] = (720, 420)) -> dict:
    """把一个查询结果构造成 Flint ChartAssemblyInput。

    参数
    ----
    df         : 结果 DataFrame（行 = 数据点）
    x, y, z    : 分别映射到 x / y / color(or heatmap value) 的字段名；
                 不传则按列名启发式自动挑（优先 dim_value / 第一个数值列）
    y2         : 组合图（柱+线双轴）的第二个度量字段名；传入即自动启用 Combo Chart
    chart_type : 显式指定（如 "Bar Chart"/"Pie Chart"/"Combo Chart"）；不传则自动推导
    title      : 图表标题
    """
    if df.empty:
        raise ValueError("空 DataFrame，无法构建图表 spec")

    # 自动选轴：x 优先 dim_value / 第一个字符串低基数列，y 优先第一个数值列
    if x is None:
        x = _auto_pick_cat(df)
    if y is None:
        y = _auto_pick_num(df)
    if x is None or y is None:
        raise ValueError(f"无法自动推断轴：x={x}, y={y}（请显式传 x/y）")

    # 语义类型：对所有列标注（Flint 只认列名 → 语义类型）
    semantic_types = {c: infer_semantic(c, df[c]) for c in df.columns}

    # 图表类型：y2 存在（组合图）→ Combo Chart；否则按数据自动推导
    ct = chart_type
    if not ct:
        if y2 and y2 in df.columns:
            ct = "Combo Chart"
        else:
            ct = infer_chart_type(df, x, y, z)

    # 编码通道
    encodings: dict = {"x": {"field": x}, "y": {"field": y}}
    if z and z in df.columns:
        encodings["color"] = {"field": z}
    if y2 and y2 in df.columns:
        encodings["y2"] = {"field": y2}

    # 语义 → 调色板自动映射（写入 spec.palette_hint，render_flint.mjs 在 auto 时采用）
    palette_hint = infer_palette(df, x, y, z, ct, semantic_types)

    # 清洗 NaN/Inf → None（pandas 默认把 NaN 写成非法 JSON 字面量，会卡死 Flint 解析）
    def _clean(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    records = [{k: _clean(val) for k, val in row.items()}
               for row in df.to_dict(orient="records")]

    spec = {
        "data": {"values": records},
        "semantic_types": semantic_types,
        "chart_spec": {
            "chartType": ct,
            "encodings": encodings,
            "baseSize": {"width": base_size[0], "height": base_size[1]},
            "title": title,
        },
        "palette_hint": palette_hint,
    }
    return spec


# --------------------------------------------------------------------------
# 5. 语义 → 调色板自动映射
#    让"评分列自动走顺序蓝、计数排名走领奖台"这类业务直觉在 spec 里固化，
#    渲染端（render_flint.mjs）在 palette=auto 时采用本提示。
# --------------------------------------------------------------------------
def infer_palette(df: pd.DataFrame, x: str, y: Optional[str], z: Optional[str],
                  chart_type: str, semantic_types: dict) -> str:
    """根据字段语义推荐调色板名称。

    规则：
    - 饼图/环形图（Pie/Donut）→ categorical（每个扇区一个分类色，最清晰）。
    - 面积图（Area）→ ramp（按数值做品牌蓝渐变填充）。
    - 组合图（Combo，柱+线双轴）→ combo（固定：柱品牌蓝、线橙色对比）。
    - 多色编码（z 有值）：颜色字段是 Score/Percentage → ramp（顺序蓝），
      否则 → categorical（精选分类色板）。
    - 热力图 → ramp（顺序）。
    - 单系列柱/条：y 度量是 Score/Percentage → ramp（评分/占比走顺序蓝）；
      其余计数/金额类 → rank（领奖台高亮前 3）。
    - 散点 → categorical。
    """
    if chart_type in ("Pie Chart", "Donut Chart"):
        return "categorical"
    if chart_type == "Area Chart":
        return "ramp"
    if chart_type == "Combo Chart":
        return "combo"
    if z and z in df.columns:
        zs = semantic_types.get(z, "")
        return "ramp" if zs in ("Score", "Percentage") else "categorical"
    if chart_type == "Heatmap":
        return "ramp"
    if chart_type == "Scatter Plot":
        return "categorical"
    if y and y in df.columns:
        # ★ cnt（门店数/计数，排名语境）→ 领奖台高亮前 3；
        #   评分/客流/潜力等 magnitude → 顺序蓝渐变（对齐"客流/潜力也走顺序蓝"的设计意图）
        return "rank" if y == "cnt" else "ramp"
    return "rank"


def _auto_pick_cat(df: pd.DataFrame) -> Optional[str]:
    if "dim_value" in df.columns:
        return "dim_value"
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            return c
    return None


def _auto_pick_num(df: pd.DataFrame) -> Optional[str]:
    if "cnt" in df.columns:
        return "cnt"
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            return c
    return None


# --------------------------------------------------------------------------
# 4. 便捷：spec 落盘 / 读取
# --------------------------------------------------------------------------
def write_spec(spec: dict, path: str | Path) -> Path:
    p = Path(path)
    # allow_nan=False 作为安全网：若仍有非有限值，直接在落盘时报错而非写出非法 JSON
    p.write_text(json.dumps(spec, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return p


def build_and_write(df: pd.DataFrame, path: str | Path, **kw) -> Path:
    spec = build_flint_spec(df, **kw)
    return write_spec(spec, path)


if __name__ == "__main__":
    # 自测：构造一个迷你数据集，验证 spec 生成
    demo = pd.DataFrame({
        "dim_value": ["瑞幸", "星巴克", "巴比馒头", "肯德基"],
        "cnt": [1822, 1192, 1056, 687],
        "avg_rating": [4.2, 4.5, 4.0, 4.1],
    })
    s = build_flint_spec(demo, title="Demo 连锁品牌门店数")
    print(json.dumps(s, ensure_ascii=False, indent=2)[:800])
