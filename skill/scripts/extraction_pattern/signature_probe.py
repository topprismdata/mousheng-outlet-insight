#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单文件「字段种类签名」探针 —— 把用户的判读方法工程化:
  "看每列的非空情况 + 字段种类, 基本就能判断这份导出的结构模式"。

对单个导出 CSV, 按【字段种类】分组, 打印每列的:
  非空率 + 取值集中度(单值占比) + 去重数 + 取值样例
直接显示: 维度列(渠道组/经营场所/省份)看"取值集中度", enrichment列(携程/点评)看"非空率"。

注意: 本探针只描述导出结构, 不推断业务人员的查询动机或决策目的。

用法:
  $PY signature_probe.py --file /path/to/one.csv
"""
import argparse, json, os
from collections import defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DICT = os.path.join(HERE, "field_dictionary.json")


def main():
    ap = argparse.ArgumentParser(description="单文件字段种类签名探针")
    ap.add_argument("--file", required=True, help="待探测的单个 售点洞察*.csv")
    ap.add_argument("--dict", default=DEFAULT_DICT, help="L2 字段字典路径")
    args = ap.parse_args()

    DICT = json.load(open(args.dict, encoding="utf-8"))["fields"]
    df = pd.read_csv(args.file, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    print(f"\n{'='*72}\n{os.path.basename(args.file)}  | 行={len(df)} 列={len(df.columns)}\n{'='*72}")

    def profile(col):
        s = df[col].astype(str).str.strip()
        ne = int((s != "").sum())
        rate = ne / len(df)
        nuniq = int(s[s != ""].nunique())
        top = ""
        if ne > 0:
            top = s[s != ""].value_counts().index[0][:16]
        return ne, rate, nuniq, top

    rows = []
    for col in df.columns:
        g = DICT.get(col, {}).get("group", "(未收录)")
        ne, rate, nu, top = profile(col)
        rows.append((g, col, ne, rate, nu, top))
    rows.sort(key=lambda r: (r[0], -r[3]))

    print(f"{'字段':<24}{'种类':<14}{'非空':>6}{'非空率':>8}{'去重':>6}  取值样例")
    cur = None
    for g, col, ne, rate, nu, top in rows:
        if g != cur:
            print(f"  ── 种类: {g} ──")
            cur = g
        print(f"{col:<24}{g:<14}{ne:>6}{rate:>7.0%}{nu:>6}  {top}")

    # 快捷结构归类 (客观, 不含意图解读)
    n = len(df)
    chan_dom, top_chan = _share(df, "渠道组")
    prov_dom, top_prov = _share(df, "省份")
    if n <= 5:
        pattern = "A-单行/少行导出"
    elif chan_dom >= 0.9:
        pattern = f"B-单一维度主导导出({top_chan})"
    elif n >= 200 and chan_dom < 0.85:
        pattern = "C-多维度分散导出"
    else:
        pattern = "D-混合导出"
    print(f"\n快捷结构归类: 行={n} 渠道组集中度={chan_dom:.0%}({top_chan}) 省份集中度={prov_dom:.0%}({top_prov}) → {pattern}")


def _share(df, col):
    if col not in df.columns:
        return 0.0, ""
    s = df[col].astype(str).str.strip()
    s = s[s != ""]
    if len(s) == 0:
        return 0.0, ""
    vc = s.value_counts(normalize=True)
    return float(vc.iloc[0]), str(vc.index[0])


if __name__ == "__main__":
    main()
