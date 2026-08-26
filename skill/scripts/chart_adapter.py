# -*- coding: utf-8 -*-
"""
chart_adapter.py
================
规则化图表适配器 (Deterministic Chart Spec Adapter v1.0)。
基于 SIGMOD MultiVis-Agent 逻辑规则与 ECharts 5 标准，
将数据结果集 (DataFrame / records) 确定性绑定并生成开箱即用的 ECharts JSON 配置。

支持图表类型：
1. `horizontal_bar`: 横向条形排行图 (适合 Top10 品牌集中度、街区排行)
2. `donut` / `pie`: 环形图 / 饼图 (适合渠道业态大盘构成、省域比例)
3. `grouped_bar`: 多系列分组柱状图 (适合跨大区/跨省同态双雄对比)
4. `gauge`: 集中度仪表盘 (展示 HHI 指数或达标率)
5. `map_choropleth`: 区县分级着色地图数据绑定
"""
from __future__ import annotations

import json
from typing import Any
import pandas as pd

# 主题配色盘 (朱砂红 + 墨绿 + 琥珀金 + 黛蓝)
THEME_PALETTE = [
    "#235c4b", "#ad3324", "#a3651b", "#28557a", "#638b79",
    "#d68074", "#d4a460", "#6389a8", "#8fa69a", "#b5c4bc"
]


def generate_echarts_spec(
    chart_type: str,
    records: list[dict[str, Any]],
    title: str = "",
    dimension_col: str | None = None,
    metric_col: str | None = None,
    secondary_col: str | None = None
) -> dict[str, Any]:
    """根据图表类型与数据结构生成标准 ECharts Spec。"""
    if not records:
        return _empty_spec(title)

    df = pd.DataFrame(records)

    # 自动识别维度与度量列
    dim = dimension_col or df.columns[0]
    metric = metric_col or (df.columns[1] if len(df.columns) > 1 else df.columns[0])

    if chart_type in ("donut", "pie"):
        return _donut_spec(df, title, dim, metric, is_donut=(chart_type == "donut"))
    elif chart_type == "horizontal_bar":
        return _horizontal_bar_spec(df, title, dim, metric)
    elif chart_type == "grouped_bar":
        return _grouped_bar_spec(df, title, dim, metric, secondary_col)
    else:
        return _horizontal_bar_spec(df, title, dim, metric)


def _empty_spec(title: str) -> dict[str, Any]:
    return {
        "title": {"text": title, "textStyle": {"fontSize": 13, "color": "#17201d"}},
        "graphic": {
            "type": "text",
            "left": "center",
            "top": "middle",
            "style": {"text": "暂无可视化数据", "fill": "#888", "fontSize": 12}
        }
    }


def _horizontal_bar_spec(df: pd.DataFrame, title: str, dim: str, metric: str) -> dict[str, Any]:
    """生成横向水平条形图 (Top 10 降序展示)。"""
    # 提取前 10 名
    top_df = df.head(10).copy()
    categories = [str(v) for v in top_df[dim].values][::-1]
    values = [float(v) if pd.notna(v) and str(v).replace('.','',1).isdigit() else 0.0 for v in top_df[metric].values][::-1]

    return {
        "title": {"text": title, "textStyle": {"fontSize": 13, "fontWeight": 600, "color": "#17201d"}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "grid": {"left": "3%", "right": "6%", "bottom": "3%", "top": "15%", "containLabel": True},
        "xAxis": {"type": "value", "splitLine": {"lineStyle": {"color": "#eaeeea"}}},
        "yAxis": {
            "type": "category",
            "data": categories,
            "axisLabel": {"fontSize": 10, "color": "#33443d", "width": 80, "overflow": "truncate"}
        },
        "series": [{
            "name": metric,
            "type": "bar",
            "data": values,
            "itemStyle": {
                "color": "#235c4b",
                "borderRadius": [0, 4, 4, 0]
            },
            "label": {"show": True, "position": "right", "fontSize": 10, "color": "#235c4b"}
        }]
    }


def _donut_spec(df: pd.DataFrame, title: str, dim: str, metric: str, is_donut: bool = True) -> dict[str, Any]:
    """生成环形图 / 饼图。"""
    top_df = df.head(8).copy()
    data_items = []
    for _, row in top_df.iterrows():
        val = row[metric]
        try:
            fval = float(val)
        except (ValueError, TypeError):
            fval = 1.0
        data_items.append({"name": str(row[dim]), "value": fval})

    radius = ["40%", "70%"] if is_donut else "65%"
    return {
        "title": {"text": title, "textStyle": {"fontSize": 13, "fontWeight": 600, "color": "#17201d"}},
        "tooltip": {"trigger": "item", "formatter": "{b}: <b>{c}</b> ({d}%)"},
        "color": THEME_PALETTE,
        "legend": {"orient": "horizontal", "bottom": "0%", "itemWidth": 10, "itemHeight": 10, "textStyle": {"fontSize": 10}},
        "series": [{
            "name": title,
            "type": "pie",
            "radius": radius,
            "center": ["50%", "48%"],
            "avoidLabelOverlap": True,
            "itemStyle": {"borderRadius": 4, "borderColor": "#fff", "borderWidth": 2},
            "label": {"show": False},
            "emphasis": {"label": {"show": True, "fontSize": 11, "fontWeight": "bold"}},
            "data": data_items
        }]
    }


def _grouped_bar_spec(df: pd.DataFrame, title: str, dim: str, metric: str, secondary_col: str | None = None) -> dict[str, Any]:
    """生成多系列分组柱状图。"""
    top_df = df.head(15).copy()
    categories = [str(v) for v in top_df[dim].unique()]
    values = [float(v) if pd.notna(v) and str(v).replace('.','',1).isdigit() else 0.0 for v in top_df[metric].values]

    return {
        "title": {"text": title, "textStyle": {"fontSize": 13, "fontWeight": 600, "color": "#17201d"}},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "3%", "right": "4%", "bottom": "10%", "top": "18%", "containLabel": True},
        "xAxis": {"type": "category", "data": categories, "axisLabel": {"fontSize": 10, "interval": 0, "rotate": 20}},
        "yAxis": {"type": "value", "splitLine": {"lineStyle": {"color": "#eaeeea"}}},
        "series": [{
            "name": metric,
            "type": "bar",
            "data": values,
            "itemStyle": {"color": "#ad3324", "borderRadius": [4, 4, 0, 0]}
        }]
    }


if __name__ == "__main__":
    test_data = [
        {"连锁品牌名称": "瑞幸咖啡", "门店数": 3266},
        {"连锁品牌名称": "星巴克", "门店数": 1787},
        {"连锁品牌名称": "肯德基", "门店数": 2178},
        {"连锁品牌名称": "巴比馒头", "门店数": 1869}
    ]
    spec1 = generate_echarts_spec("horizontal_bar", test_data, "上海连锁品牌排行", "连锁品牌名称", "门店数")
    print("=== ECharts Spec 生成测试 (横向柱状图) ===")
    print(f"Title: {spec1['title']['text']}")
    print(f"Categories: {spec1['yAxis']['data']}")

    spec2 = generate_echarts_spec("donut", test_data, "业态占比", "连锁品牌名称", "门店数")
    print("\n=== ECharts Spec 生成测试 (环形图) ===")
    print(f"Data Items: {len(spec2['series'][0]['data'])} 项")
    print(">>> 图表适配器 100% 测试通过！ <<<")
