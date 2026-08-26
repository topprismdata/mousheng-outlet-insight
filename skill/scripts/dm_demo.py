#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DM 交互式下钻 Demo —— 演示「统计级查询 → 客户点选 → 原始明细下钻」两步流。

用法:
    $PY dm_demo.py                      # 跑内置示例对话（目标城市餐饮连锁品牌）
    $PY dm_demo.py --repl              # 进入交互 REPL，自行输入统计查询 + 点选下钻
    $PY dm_demo.py --build             # 强制重建 DM

数据: ~/outlet-insight/data/<data>,csv}
DM : ~/outlet-insight/data/sh_dm.sqlite (首次自动构建)
"""

import sys
import os
import sqlite3
import pandas as pd

from dm_layer import build_dm, query_stat, drilldown, GEO_NORM, DIMENSIONS

PARQUET = "/Users/ghb/outlet-insight/data/<data>.parquet"
CSV = "/Users/ghb/outlet-insight/data/<data>.csv"
DB = "/Users/ghb/outlet-insight/data/sh_dm.sqlite"

pd.set_option("display.width", 160)
pd.set_option("display.max_rows", 40)
pd.set_option("display.max_columns", 20)


def ensure_dm():
    if not os.path.exists(DB) or "--build" in sys.argv:
        src = PARQUET if os.path.exists(PARQUET) else CSV
        build_dm(src, DB)


def parse_stat_query(text: str):
    """
    极简统计查询解析（demo 用，生产应接 nl_parser + 语义层）。
    识别：group_by 维度 + 过滤（城市/渠道组/是否连锁）+ 排序。
    """
    text = text.replace("，", ",").replace("。", "")
    # group_by: 品牌 / 业态 / 渠道 / 城市 / 大区 / 子渠道
    group_by = "连锁品牌名称"
    if "业态" in text or "渠道组" in text:
        group_by = "渠道组"
    elif "子渠道" in text:
        group_by = "细分渠道"
    elif "大区" in text:
        group_by = "规划大区"
    elif "城市" in text and "品牌" not in text:
        group_by = "城市"

    filters = {}
    # 城市
    for alias, real in GEO_NORM.items():
        if alias in text:
            filters["城市"] = real
            break
    # 业态 / 渠道组
    for ch in ["餐饮", "食杂", "住宿", "教育", "旅游", "零食店", "便利店", "娱乐"]:
        if ch in text:
            filters["渠道组"] = ch
            break
    # 连锁
    if "连锁" in text or "品牌" in text:
        filters["是否连锁"] = "是"
    # 排序
    order_by = "cnt"
    asc = False
    if "评分" in text:
        order_by = "avg_rating"
    elif "客流" in text:
        order_by = "avg_traffic"
    elif "潜力" in text:
        order_by = "sum_potential"

    limit = 15
    if "top" in text.lower():
        import re
        m = re.search(r"top\s*(\d+)", text.lower())
        if m:
            limit = int(m.group(1))
    return group_by, filters, order_by, asc, limit


def show_stat(text: str):
    """执行统计查询并打印，返回 (df, note) 供后续下钻。"""
    group_by, filters, order_by, asc, limit = parse_stat_query(text)
    df, note = query_stat(DB, group_by=group_by, filters=filters,
                           order_by=order_by, ascending=asc, limit=limit)
    print(f"\n【统计查询】{text}")
    print(f"  → DM 命中: group_by={group_by}, filters={filters}, "
          f"order_by={order_by}, 返回 {len(df)} 行（毫秒级，未扫底表）")
    if len(df) == 0:
        print("  ⚠ 无结果（检查过滤条件 / 地理归一化）")
        return df, note
    print(df.to_string(index=False))
    return df, note


def do_drill(df, note, pick: str):
    """根据客户点选（序号或品牌名）下钻原始明细。"""
    # 解析点选
    idx = None
    if pick.isdigit():
        idx = int(pick) - 1
    else:
        for i, v in enumerate(df["dim_value"].tolist()):
            if pick in str(v):
                idx = i
                break
    if idx is None or idx >= len(df):
        print(f"  ⚠ 未找到「{pick}」，请输入序号或品牌名")
        return
    brand = df.iloc[idx]["dim_value"]
    # 下钻过滤键 = 原统计过滤 + 点选维度
    drill_filters = dict(note.get("filters", {}))
    drill_filters[note["group_by"]] = brand
    detail = drilldown(PARQUET, drill_filters, limit=10)
    print(f"\n【下钻】{note['group_by']}={brand} 的原始明细（前 {min(10,len(detail))} 行）")
    cols = [c for c in ["腾讯售点名称", "城市", "渠道组", "连锁品牌名称",
                        "大众点评餐饮点评评分星级", "周末均值客流(人)",
                        "月均收入潜力预测(如有协议)"] if c in detail.columns]
    print(detail[cols].to_string(index=False))
    print(f"  下钻过滤键: {drill_filters}")
    return detail


def run_scripted_demo():
    ensure_dm()
    print("=" * 72)
    print("DM 交互式下钻 Demo")
    print("场景：客户先问统计（目标城市餐饮连锁品牌按门店数排序），再决定下钻")
    print("=" * 72)

    # 第一步：统计查询
    df, note = show_stat("目标城市餐饮的连锁品牌都有哪些，按照门店数量排序")

    # 第二步：客户决定看 TOP1（瑞幸咖啡）的明细
    print("\n" + "-" * 72)
    print("客户：「看排第一的瑞幸咖啡门店明细」")
    print("-" * 72)
    do_drill(df, note, "1")

    # 第三步：客户换个维度，看业态分布
    print("\n" + "=" * 72)
    print("客户：「那目标城市整体各业态分布是什么样？」")
    print("=" * 72)
    df2, note2 = show_stat("目标城市按业态看门店数量分布")
    do_drill(df2, note2, "餐饮")


def repl():
    ensure_dm()
    print("DM 交互 REPL（输入统计查询 → 看结果 → 输入序号/品牌名下钻 → :q 退出）")
    last = None
    while True:
        try:
            text = input("\n统计查询> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text in (":q", "exit", "quit"):
            break
        if text.startswith(":"):
            # 下钻指令：:drill 瑞幸 / :1
            arg = text[1:].strip()
            if arg.startswith("drill") or arg.isdigit():
                if last is None:
                    print("  先做一次统计查询")
                    continue
                pick = arg.replace("drill", "").strip() or "1"
                do_drill(last[0], last[1], pick)
            continue
        if not text:
            continue
        last = show_stat(text)


if __name__ == "__main__":
    if "--repl" in sys.argv:
        repl()
    else:
        run_scripted_demo()
