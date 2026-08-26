#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建 售点洞察 字段语义字典 (L2 元数据层, 零 LLM)。
输出: field_dictionary.json —— 每个字段的 业务分组 / 语义 / 是否可作为筛选维度。

业务字段会随版本演进, 故保留此生成器: 拿到一份含全部 <n_cols> 列的基准导出,
重跑即可刷新字典。规则+手工校订混合构建。

用法:
  $PY build_field_dictionary.py --src /path/to/one_full_export.csv --out ./field_dictionary.json
"""
import argparse, json, os
from collections import Counter

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.expanduser("~/outlet-insight/data/exports_89/售点洞察1784601676919.csv")
DEFAULT_OUT = os.path.join(HERE, "field_dictionary.json")

GROUPS = {
    "identity":      "售点身份标识",
    "org_location":  "组织与地理(省/大区/OU/线路)",
    "channel":       "渠道与业态分类",
    "chain":         "连锁/KA品牌",
    "service":       "服务状态与经营场所",
    "tourism":       "携程旅游住宿",
    "dianping":      "大众点评(餐饮评测)",
    "potential":     "收入潜力与消费水平",
    "population":    "人口/客流/周边 proximity",
    "geo":           "乡镇地理属性",
    "sales":         "销售/协议/冰柜/排面",
    "visit":         "拜访/回传/建档/客资",
    "o2o":           "O2O 业态",
    "other":         "其他/备注",
}

FILTER_DIMS = {
    "省份", "规划大区", "渠道组", "细分渠道", "经营场所", "服务状态",
    "连锁品牌名称", "携程-类型", "携程-酒店/民宿分类", "携程-星级",
    "城市", "区县", "KA名称", "是否连锁", "市场区隔", "经营场所类型",
}


def assign_group(col: str) -> str:
    c = col
    if c.startswith("携程"):
        return "tourism"
    if c.startswith("大众点评"):
        return "dianping"
    if c in {"省份", "城市", "区县", "规划OU", "规划大区", "规划办事处",
             "规划主管", "规划线路", "RTMID", "KO_ID"}:
        return "org_location"
    if c in {"腾讯售点id", "腾讯售点名称", "腾讯售点地址", "电话"}:
        return "identity"
    if c in {"一级分类", "二级分类", "三级分类", "渠道组", "渠道", "子渠道",
             "细分渠道", "待开发客户细分"}:
        return "channel"
    if c in {"是否连锁", "连锁品牌ID", "连锁品牌名称", "连锁集团ID", "连锁集团名称",
             "连锁品牌信息备注", "连锁来源", "KA名称", "连锁名称", "连锁类型",
             "重点客户类型名称", "客户集团", "连锁范围名称", "重点业态分类名称"}:
        return "chain"
    if c in {"服务状态", "经营场所", "经营场所名称", "经营场所ID"}:
        return "service"
    if ("月均收入潜力预测" in c or c.startswith("NIQ") or "消费水平" in c
            or "居住社区房价" in c or "居住酒店" in c or c.startswith("LINX")
            or "NARTD指数" in c):
        return "potential"
    if (c.startswith("示范饮料") or c.startswith("系统乡镇") or c.startswith("据DC")
            or c.startswith("据办事处") or c in {"市场区隔", "乡镇属性", "赶集日",
            "是否红镇，默认否", "到访偏好", "职住直线距离"}
            or c.startswith("非密集区域乡镇客户")):
        return "geo"
    if ("人口" in c or "客流" in c or "占比" in c
            or any(k in c for k in ["外50米", "外500米", "外1公里", "外2公里", "外100米"])
            or c in {"居住社区房价等级", "居住酒店档次", "社区居住房价", "居住酒店价格"}):
        return "population"
    if (any(k in c for k in ["协议", "冰柜", "TOP", "排面", "销售收入", "配送",
            "客户状态码", "攻坚", "闪电仓", "重点客户"]) or c in {"未拿到费用协议客户"}):
        return "sales"
    if (any(k in c for k in ["拜访", "OU回传", "手机端反馈", "POI上线", "建档", "客资"])
            or c in {"区域线路编码", "区域线路办事处"}):
        return "visit"
    if c.startswith("是否是O2O") or c.startswith("是否商超O2O"):
        return "o2o"
    return "other"


def main():
    ap = argparse.ArgumentParser(description="构建售点洞察字段语义字典")
    ap.add_argument("--src", default=DEFAULT_SRC, help="含全部 <n_cols> 列的基准导出 CSV")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出 JSON 路径")
    args = ap.parse_args()

    df = pd.read_csv(args.src, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    cols = list(df.columns)
    dictionary = {c: {
        "group": assign_group(c),
        "is_filter_dimension": c in FILTER_DIMS,
        "semantic": GROUPS[assign_group(c)],
    } for c in cols}
    grp_count = Counter(v["group"] for v in dictionary.values())
    meta = {
        "source": os.path.basename(args.src),
        "total_columns": len(cols),
        "group_legend": GROUPS,
        "group_field_count": dict(grp_count),
        "filter_dimensions": sorted(FILTER_DIMS),
        "note": "L2 字段语义字典: 由规则+手工校订生成, 可随业务扩展。扫描脚本用它解码'哪些语义组被深拉(填充率>=50%)'以客观识别导出结构模式。",
    }
    json.dump({"meta": meta, "fields": dictionary}, open(args.out, "w"), ensure_ascii=False, indent=2)
    print(f"字段字典已写出: {args.out}")
    print(f"  总字段={len(cols)}  分组数={len(grp_count)}")
    for g, n in grp_count.most_common():
        print(f"    {g:12s} {n:3d}  {GROUPS[g]}")
    print(f"  筛选维度({len(FILTER_DIMS)}): {sorted(FILTER_DIMS)}")


if __name__ == "__main__":
    main()
