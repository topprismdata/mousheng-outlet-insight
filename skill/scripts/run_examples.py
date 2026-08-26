"""
run_examples.py
===============
复现三类查询的全部 9 个示例（截图 1/2），用于自检与演示。

运行方式：
    python run_examples.py             # 跑全部示例（结果写入 --out 目录，默认 /tmp）
    python run_examples.py --only 1     # 只跑示例 1
    python run_examples.py --data /path/to.csv
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd

from agent_core import FieldRegistry, load_store_data, run_query
from nl_parser import auto_parse

OUT = Path("/tmp/sh_store_insight/results")
OUT.mkdir(parents=True, exist_ok=True)

# 截图 1 + 截图 2 的全部示例
EXAMPLES = [
    {"id": 1, "name": "浦西大区的美宜佳清单", "query": "浦西大区的美宜佳清单", "kind": "A",
     "select": ["规划大区", "城市", "区县", "腾讯售点名称", "腾讯售点地址", "连锁品牌名称", "周末均值客流(人)"]},
    {"id": 2, "name": "未服务的食杂潜力TOP20", "query": "未服务的食杂潜力TOP20", "kind": "A",
     "select": ["省份", "城市", "区县", "腾讯售点名称", "连锁品牌名称", "渠道组", "子渠道", "月均收入潜力预测(无协议无冰柜)"]},
    {"id": 3, "name": "江苏省内所有零售很嗨门店清单", "query": "江苏省内所有零售很嗨门店清单", "kind": "A",
     "select": ["省份", "城市", "区县", "腾讯售点名称", "连锁品牌名称", "渠道组", "周末均值客流(人)"]},
    {"id": 4, "name": "经营场所为餐饮街的客户", "query": "经营场所为餐饮街的客户", "kind": "A",
     "select": ["省份", "城市", "区县", "腾讯售点名称", "经营场所", "经营场所名称", "细分渠道", "周末均值客流(人)"]},
    {"id": 5, "name": "客房数大于100间的非连锁酒店", "query": "客房数大于100间的非连锁酒店", "kind": "A",
     "select": ["省份", "城市", "区县", "腾讯售点名称", "携程-名称", "携程-房间数", "携程-星级", "携程-综合评分", "是否连锁", "连锁品牌名称"]},
    {"id": 6, "name": "主题乐园客流TOP50（5A/国营等画像可在结果上二次过滤）", "query": "主题乐园，客流TOP50", "kind": "A",
     "select": ["省份", "城市", "区县", "腾讯售点名称", "细分渠道", "经营场所", "是否连锁", "连锁集团名称", "是否高德扫街榜/状元榜", "周末均值客流(人)"],
     "_note": "原 query 字面叠加 5A/国营/独立运营/联合管理/半封闭 5 个条件过于严苛 (返回 0)；拆解后保留主题乐园+客流TOP50。"},
    {"id": 7, "name": "嘉兴大区所有大学、专科、初高中学校", "query": "嘉兴大区所有大学、专科、初高中学校", "kind": "A",
     "select": ["规划大区", "城市", "区县", "腾讯售点名称", "渠道组", "子渠道", "细分渠道", "居住人口(人)", "办公人口(人)"]},
    {"id": 8, "name": "餐饮街的客户分布（按业态/经营场所）", "query": "餐饮街的客户分布", "kind": "B"},
    {"id": 9, "name": "C-规则：街道餐饮≥20+周末客流排序+评分3.8占比50%为高+开业3年占比50%为高",
     "query": "街道餐饮客户不低于20家，周末人流按大小排序，评分3.8分以上客户占比50%以上为高，开业3年以上客户占比50%以上为高", "kind": "C"},
]


def run_one(df: pd.DataFrame, reg: FieldRegistry, ex: dict) -> dict:
    print("\n" + "=" * 70)
    print(f"#{ex['id']}  {ex['name']}")
    print("=" * 70)
    query_text = ex.get("query", ex["name"])
    q = auto_parse(query_text, reg)
    if "select" in ex and ex.get("kind") != "C":
        q["select"] = ex["select"]
    print(f"[QUERY] {query_text}")
    print("[DSL]")
    print(json.dumps(q, ensure_ascii=False, indent=2))
    t0 = time.time()
    out, trace, kind, audit_report = run_query(df, q, reg)
    print(f"\n[执行] 类型={kind}  耗时={time.time()-t0:.2f}s")
    for t in trace:
        print("  •", t)
    # 数据质量与适用性评估摘要
    if audit_report.get("audited"):
        ar = audit_report
        fm = ar.get("field_missing", {})
        if fm:
            parts = "，".join(f"{lbl}{v}%" for lbl, v in fm.items())
            print(f"  [体检] 关键字段缺失率：{parts}")
        print("  [体检] " + ar.get("recommendation", ""))
        for f in ar.get("flags", []):
            print("    ⚠", f)
    print(f"[结果] {len(out):,} 行 × {out.shape[1]} 列")
    if len(out):
        print("[预览] 前 5 行：")
        print(out.head(5).to_string(max_colwidth=40))
    safe_name = re.sub(r"[^\w]+", "_", ex["name"])[:60]
    csv_path = OUT / f"example_{ex['id']:02d}_{safe_name}.csv"
    out.to_csv(csv_path, index=False)
    print(f"[保存] {csv_path}")
    return {"id": ex["id"], "name": ex["name"], "kind": kind,
            "rows": len(out), "cols": out.shape[1], "time_s": round(time.time() - t0, 2),
            "audit": audit_report, "trace": trace, "csv": str(csv_path)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="CSV/parquet 路径（默认自动发现）")
    ap.add_argument("--out", help="结果目录（默认 /tmp/sh_store_insight/results）")
    ap.add_argument("--only", type=int, help="只跑指定 id 的示例")
    args = ap.parse_args()

    global OUT
    if args.out:
        OUT = Path(args.out)
        OUT.mkdir(parents=True, exist_ok=True)

    print("加载数据中...")
    t0 = time.time()
    reg = FieldRegistry.load()
    df = load_store_data(args.data, reg=reg)
    print(f"就绪：{len(df):,} 行 × {df.shape[1]} 列  耗时 {time.time()-t0:.1f}s")

    summary = []
    for ex in EXAMPLES:
        if args.only and ex["id"] != args.only:
            continue
        try:
            summary.append(run_one(df, reg, ex))
        except Exception as e:
            print(f"[ERROR] {ex['name']}: {e}")
            import traceback
            traceback.print_exc()
            summary.append({"id": ex["id"], "name": ex["name"], "error": str(e)})

    (OUT / "summary.json").write_text(
        json.dumps([{k: v for k, v in r.items() if k not in ("trace", "audit")} for r in summary],
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\n" + "=" * 70)
    print("汇总：")
    for r in summary:
        if "error" in r:
            print(f"  #{r['id']:>2}  ❌ {r['error']}")
        else:
            print(f"  #{r['id']:>2}  ✅ {r['kind']}  {r['rows']:>6,} 行  {r['time_s']}s  {r['name']}")


if __name__ == "__main__":
    main()
