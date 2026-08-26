"""
agent_cli.py
============
售点洞察通用查询智能体 — 命令行入口（供智能体或直接调用）。

单条查询（向后兼容）：
    python agent_cli.py -q "目标子区域B的美宜佳清单" --data /path/to.csv --out /tmp/r.csv

多步会话（推荐：会话态 + Dry-plan + RAG 记忆）：
    python agent_cli.py -s --data /path/to.csv

其他：
    --plan          单条查询前打印 Dry-plan 透明执行计划
    --memory-reset  清空 RAG 黄金查询记忆库
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from agent_core import FieldRegistry, load_store_data, validate_query, find_default_data
from nl_parser import auto_parse, explain_query
from session import QuerySession
from engine import run_query as _eng_run_query  # 第6块：可选 DataFusion 后端
from memory import QueryMemory
from profiler import load_profile, set_profile, gen_schema_md  # 第5块：统计/画像层


def print_semantic_check(reg: FieldRegistry, df) -> None:
    """离线探索期语义层自检（对应论文 Data-Copilot 的 exploration phase）。"""
    chk = reg.validate_against(df)
    print("【语义层自检】"
          f" 注册字段 {chk['registered_fields']} 全部命中物理表"
          if not chk["missing_fields"] else
          f" 注册字段 {chk['registered_fields']}，缺失 {chk['missing_fields']}")
    cov = chk["measure_coverage"]
    notable = {k: v for k, v in cov.items() if 0 < v < 100}
    if notable:
        print("  业务度量覆盖率（部分可用，需留意）: " +
              "，".join(f"{k} {v:.0f}%" for k, v in notable.items()))
    else:
        print("  业务度量覆盖率: 全部 0% 或 100%（无部分缺口）")


DEFAULT_TOP = 20


def show_table(out: pd.DataFrame, head: int = DEFAULT_TOP, max_cols: int = 20) -> str:
    if out.shape[1] > max_cols:
        out_view = out.iloc[:, :max_cols]
        col_note = f"（仅展示前 {max_cols}/{out.shape[1]} 列；用 --all-cols 看全部）"
    else:
        out_view = out
        col_note = ""
    s = out_view.head(head).to_string(max_colwidth=40)
    n = len(out)
    return f"\n[结果] {n:,} 行 × {out.shape[1]} 列{col_note}\n" + (
        f"[预览前 {head} 行]\n{s}" if n else "(无数据)"
    )


def one_query(text: str, df: pd.DataFrame, reg: FieldRegistry, *,
              top_n: int = DEFAULT_TOP, out_csv: Path | None = None,
              all_cols: bool = False, as_json: bool = False,
              engine: str = "pandas") -> dict:
    """执行单条查询（无状态，向后兼容）。engine 可选 pandas/datafusion。"""
    t0 = time.time()
    q = auto_parse(text, reg)
    result: dict = {"query": text, "dsl": q}
    try:
        out, trace, kind, audit_report = _eng_run_query(df, q, reg, engine=engine)
    except Exception as e:
        result.update({"ok": False, "error": str(e), "trace": []})
        print(json.dumps(result, ensure_ascii=False))
        return result

    from adapters.legacy_dataframe_api import synthesize
    from insight import print_insight
    result.update({
        "ok": True, "kind": kind, "rows": int(len(out)), "cols": int(out.shape[1]),
        "columns": list(out.columns), "trace": trace,
        "audit": audit_report, "insight": insight, "time_s": round(time.time() - t0, 2),
    })
    if out_csv:
        out.to_csv(out_csv, index=False)
        result["csv"] = str(out_csv)
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("\n" + "-" * 60)
        print(f"Q: {text}")
        print("DSL:", json.dumps(q, ensure_ascii=False))
        for t in trace:
            print(" •", t)
        print(f"\n耗时 {result['time_s']}s")
        if out_csv:
            print(f"[保存] {out_csv}  ({len(out):,} 行)")
        show_fn = lambda o: print(show_table(o, head=top_n, max_cols=10**6 if all_cols else 20))
        show_fn(out)
        print_audit(audit_report)
        print_insight(insight)
    return result


def print_audit(report: dict) -> None:
    """打印数据质量与适用性评估报告。"""
    if not report.get("audited"):
        return
    print("\n" + "—" * 60)
    print("【数据质量与适用性评估】")
    fm = report.get("field_missing", {})
    if fm:
        print("  关键字段缺失率：")
        for lbl, v in fm.items():
            print(f"    · {lbl}: {v}%")
    if report.get("scope") == "candidates":
        print(f"  候选数: {report['n']}  数据完整度<{int(report['threshold']*100)}% 的候选: "
              f"{report['incomplete_count']} 个 ({report['incomplete_ratio']}%)")
    for f in report.get("flags", []):
        print(f"  ⚠ {f}")
    print(f"  → 建议: {report['recommendation']}")


def print_profile(prof: dict, field: str) -> None:
    """打印字段统计画像（CLI --profile）。"""
    if field and field != "__all__":
        st = prof.get("fields", {}).get(field)
        if not st:
            print(f"[profile] 字段 {field!r} 不在画像中（用 --profile 看全部字段名）")
            return
        print(f"\n【字段画像】{field}（{st.get('type')}）")
        print(f"  覆盖: {st.get('non_null'):,} 行，缺失率 {st.get('null_rate')}")
        if st.get("type") in ("category", "text"):
            print(f"  基数(去重): {st.get('cardinality')}")
            print("  Top 值: " + "；".join(f"{v}={c:,}" for v, c in st.get("top_values", [])[:10]))
        elif st.get("type") == "numeric":
            for k in ("min", "max", "mean", "median", "std", "p1", "p25", "p75", "p99"):
                if st.get(k) is not None:
                    print(f"  {k}: {st[k]}")
        elif st.get("type") == "bool":
            print(f"  true={st.get('true'):,}，false={st.get('false'):,}")
        if st.get("by_业态"):
            print("  按业态覆盖率: " + "；".join(f"{k}={v}" for k, v in st["by_业态"].items()))
    else:
        print(f"\n【数据画像概览】{prof.get('n_rows'):,} 行 × {prof.get('n_fields')} 字段")
        for dim, rows in prof.get("overall", {}).items():
            top = "，".join(f"{k}={v:,}" for k, v in rows[:8])
            print(f"  · {dim}: {top}")
        print(f"  共 {len(prof.get('fields', {}))} 个字段（用 --profile <字段名> 看单字段统计）")


def session_repl(data, use_cache: bool, top_n: int = DEFAULT_TOP, all_cols: bool = False,
                 use_memory: bool = True, engine: str = "pandas") -> None:
    from adapters.legacy_dataframe_api import synthesize
    from insight import print_insight
    print("初始化会话（加载全量数据）...")
    t0 = time.time()
    sess = QuerySession(data, use_cache=use_cache, engine=engine)
    set_profile(load_profile(data, sess.reg))  # 第5块：加载画像供解析路由
    print(f"就绪：{len(sess.full_df):,} 行 × {sess.full_df.shape[1]} 列  耗时 {time.time()-t0:.1f}s")
    print_semantic_check(sess.reg, sess.full_df)
    print("\n多步会话模式：支持连续追问（如『再在其中筛评分>3.8』『看它们的分布』）")
    print("指令: :reset 重置 | :history 历史 | :current 工作集规模 | :memory 记忆 |"
          " :fix <正确意图> 纠偏 | :q 退出")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye."); return
        if not text or text in (":q", "exit", "quit"):
            print("Bye."); return
        if text == ":reset":
            sess.reset(); print("已重置工作集 → 全量数据"); continue
        if text == ":history":
            print(sess.history()); continue
        if text == ":current":
            print(f"当前工作集：{len(sess.current):,} 行 × {sess.current.shape[1]} 列"); continue
        if text == ":memory":
            mem = QueryMemory()
            print(f"记忆库：{len(mem.records)} 条（最近 10 条）")
            for r in mem.records[-10:]:
                print(f"  · [{r.get('kind')}] {r['nl']}")
            continue
        if text.startswith(":fix"):
            arg = text[4:].strip()
            if not arg:
                print("用法: :fix <正确的自然语言>   或   :fix dsl <JSON DSL>")
                continue
            if arg.lower().startswith("dsl "):
                try:
                    dsl = json.loads(arg[4:].strip())
                except Exception as e:
                    print("✗ DSL JSON 解析失败:", e); continue
                r = sess.fix_last(dsl=dsl, verbose=True)
            else:
                r = sess.fix_last(correct_nl=arg, verbose=True)
            if not r.get("ok"):
                print("✗ 修正失败:", r.get("error")); continue
            print("\n" + "-" * 60)
            print(show_table(r["out"], head=top_n, max_cols=10**6 if all_cols else 20))
            print_audit(r["audit"])
            print_insight(synthesize(r.get("dsl", {}), r.get("out"), r.get("audit"), sess.reg))
            continue
        r = sess.ask(text, verbose=True, use_memory=use_memory)
        if r.get("ok"):
            print("\n" + "-" * 60)
            print(show_table(r["out"], head=top_n, max_cols=10**6 if all_cols else 20))
            print_audit(r["audit"])
            print_insight(synthesize(r.get("dsl", {}), r.get("out"), r.get("audit"), sess.reg))


def main():
    ap = argparse.ArgumentParser(description="售点洞察通用查询智能体")
    ap.add_argument("-q", "--query", help="单次查询（中文）；不传则进入 REPL")
    ap.add_argument("--data", help="CSV/parquet 路径")
    ap.add_argument("--out", help="结果 CSV 输出路径")
    ap.add_argument("-n", "--top", type=int, default=DEFAULT_TOP, help="预览行数")
    ap.add_argument("--all-cols", action="store_true", help="展示全部列")
    ap.add_argument("--no-cache", action="store_true", help="禁用 parquet 缓存")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（面向智能体）")
    ap.add_argument("-s", "--session", action="store_true", help="多步会话模式（会话态+记忆）")
    ap.add_argument("--plan", action="store_true", help="查询前打印 Dry-plan 透明执行计划")
    ap.add_argument("--memory-reset", action="store_true", help="清空 RAG 黄金查询记忆库")
    ap.add_argument("-M", "--no-memory", action="store_true",
                    help="禁用 RAG 记忆（不读不写），用于探索/测试，避免污染飞轮")
    ap.add_argument("--profile", nargs="?", const="__all__", default=None,
                    help="打印字段统计画像；可跟字段名看单个字段（如 --profile 渠道组）")
    ap.add_argument("--reprofile", action="store_true",
                    help="强制重新构建画像缓存（忽略已有 .profile.json）")
    ap.add_argument("--gen-schema", action="store_true",
                    help="用真实画像刷新 references/data_schema.md（告别手写过期数字）")
    ap.add_argument("--engine", choices=["pandas", "datafusion"], default="pandas",
                    help="计算后端：pandas（默认，零依赖）或 datafusion（Apache DataFusion "
                         "向量化引擎，适合超内存/高并发）。两者结果等价。")
    ap.add_argument("--approx", action="store_true",
                    help="近似画像模式（DataSketches）：HLL 估算去重基数、KLL 估算分位数，"
                         "适合超大数据/流式场景，不写缓存。")
    args = ap.parse_args()

    if args.memory_reset:
        QueryMemory().clear()
        print("已清空黄金查询记忆库。")
        return

    # —— 第5块：统计/画像层（--profile / --gen-schema / --reprofile）——
    if args.profile is not None or args.gen_schema or args.reprofile:
        reg = FieldRegistry.load()
        try:
            data_path = Path(args.data) if args.data else find_default_data()
        except Exception as e:
            print("未找到数据，请用 --data 指定:", e)
            return
        prof = load_profile(data_path, reg, force=args.reprofile, approx=args.approx)
        if prof is None:
            print("画像构建失败（数据加载异常）")
            return
        if args.profile is not None:
            print_profile(prof, args.profile)
        if args.gen_schema:
            out_path = Path(__file__).resolve().parent.parent / "references" / "data_schema.md"
            gen_schema_md(prof, reg, out_path)
            print(f"已用真实画像刷新：{out_path}")
        return

    if args.session:
        session_repl(args.data, use_cache=not args.no_cache,
                     top_n=args.top, all_cols=args.all_cols,
                     use_memory=not args.no_memory, engine=args.engine)
        return

    print("加载数据中...")
    t0 = time.time()
    reg = FieldRegistry.load()
    df = load_store_data(args.data, reg=reg, use_cache=not args.no_cache)
    print(f"就绪：{len(df):,} 行 × {df.shape[1]} 列  耗时 {time.time()-t0:.1f}s")
    print_semantic_check(reg, df)
    set_profile(load_profile(args.data, reg))  # 第5块：加载画像供解析路由

    if args.query:
        if args.plan:
            print("\n【Dry-plan 透明执行计划】")
            print(explain_query(args.query, reg, df))
            print()
        one_query(args.query, df, reg, top_n=args.top,
                  out_csv=Path(args.out) if args.out else None,
                  all_cols=args.all_cols, as_json=args.json, engine=args.engine)
        return

    # 旧版单条 REPL（无状态，向后兼容）
    print("\n售点洞察智能体 — 输入中文需求，Ctrl+D 退出")
    print("示例: 目标子区域B的美宜佳清单 / 客房数大于100间的非连锁酒店 /")
    print("     餐饮街的客户分布 / 街道餐饮不低于20家+评分3.8+占比50%为高")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye."); return
        if not text or text in (":q", "exit", "quit"):
            return
        one_query(text, df, reg, top_n=args.top,
                  out_csv=Path(args.out) if args.out else None,
                  all_cols=args.all_cols, engine=args.engine)


if __name__ == "__main__":
    main()
