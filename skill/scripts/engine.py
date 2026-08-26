# -*- coding: utf-8 -*-
"""
engine.py — 可选计算后端（第 6 块进阶能力：DataFusion 可选后端）。

默认 pandas（复用 agent_core.run_query，零改动、零风险）；可选 datafusion
（Apache DataFusion 54，Rust 向量化引擎）作为「超内存 / 多并发」场景的逃生舱。
两后端对相同 DSL 产出结构一致（out DataFrame 等价），下游 compute / CLI / MCP 无感。

为什么默认 pandas：当前 629K 行规模下，DataFusion 经 FFI 反而略慢（实测 5.9ms vs 4.3ms），
其真正价值在 (1) 数据暴涨超内存时流式/分区读 parquet；(2) MCP 多 agent 并发查询的吞吐；
(3) Parquet 谓词下推省 IO。故做成可选，不替换主路径。
"""
from __future__ import annotations

import tempfile
from typing import Any

import pandas as pd

from agent_core import (
    run_query as _pandas_run_query,
    audit, validate_query, _expand_semantics,
)

# ---- DataFusion 懒加载（装了才用，没装自动降级 pandas）----
_DF_AVAILABLE = False
try:
    from datafusion import SessionContext
    import pyarrow as pa
    _DF_AVAILABLE = True
except Exception:
    _DF_AVAILABLE = False

# 同一进程内 DataFrame 只序列化一次到临时 parquet（DataFusion 读 parquet 最稳）
_PARQUET_CACHE: dict[int, str] = {}


def _parquet_path(df: pd.DataFrame) -> str:
    key = id(df)
    if key not in _PARQUET_CACHE:
        p = tempfile.mktemp(suffix=".parquet")
        df.to_parquet(p, index=False)
        _PARQUET_CACHE[key] = p
    return _PARQUET_CACHE[key]


def run_query(df: pd.DataFrame, query: dict, reg,
              engine: str = "pandas",
              dm_db: str | None = None) -> tuple[pd.DataFrame, list[str], str, dict]:
    """统一查询入口。engine='pandas' | 'datafusion'。

    dm_db：可选 DM 汇总层 SQLite 路径（第 10 块）。传入且查询是「单维度分布 +
    DM 维度等值过滤 + count」时，直接命中预物化汇总表（毫秒级、不扫底表）；
    任何不可路由情形（多维/非等值/非 count/含 DM 外字段）自动回退底表扫描，
    结果与 pandas 路径等价。调用方仅应在 df 为**全量数据**时传入 dm_db
    （工作集子集上的分布必须现算，DM 是全量口径）。
    """
    if dm_db and query.get("group_by"):
        routed = _try_dm_route(df, query, reg, dm_db)
        if routed is not None:
            return routed
    if engine == "datafusion":
        return _run_query_df(df, query, reg)
    return _pandas_run_query(df, query, reg)


def _try_dm_route(df, query, reg, dm_db):
    """B 类查询 → DM 汇总表路由。不可路由/失败一律返回 None（静默回退，不破坏主路径）。"""
    try:
        from dm_layer import route_b_query
        # 与主路径同构：先语义展开 + 编译校验，护栏不因走 DM 而放松
        q = dict(query)
        if "filters" in q:
            q["filters"] = _expand_semantics(q["filters"], reg)
        v = validate_query(q, reg)
        if not v["ok"]:
            return None   # 让主路径抛出编译错误（保持报错行为一致）
        routed = route_b_query(q, reg, dm_db)
        if routed is None:
            return None
        out, trace = routed
        out, ar = audit(df, query, out, reg, "B")
        return out, trace, "A/B", ar
    except Exception:
        return None


def _run_query_df(df, query, reg):
    if not _DF_AVAILABLE:
        return _pandas_run_query(df, query, reg)
    # 1) 语义展开 + 编译校验（与 pandas 路径完全一致，保证护栏统一）
    q = dict(query)
    if "filters" in q:
        q["filters"] = _expand_semantics(q["filters"], reg)
    if "candidate_filter" in q:
        q["candidate_filter"] = _expand_semantics(q["candidate_filter"], reg)
    v = validate_query(q, reg)
    if not v["ok"]:
        raise ValueError(
            "查询未通过语义层编译校验（已在编译期拦截，避免产出错误数据）：\n"
            + "\n".join("  • " + e for e in v["errors"])
        )
    qtype = q.get("type", "A")
    if qtype in ("A", "B"):
        sql = _dsl_to_sql(q, reg)
        if sql is None:   # 含 DataFusion 暂不支持的算子（如 regex）→ 降级 pandas
            return _pandas_run_query(df, query, reg)
        ctx = SessionContext()
        ctx.register_parquet("sh", _parquet_path(df))
        batches = ctx.sql(sql).collect()
        out = pa.Table.from_batches(batches).to_pandas()
        is_dist = bool(q.get("group_by"))
        kind = "B" if is_dist else "A"
        out, ar = audit(df, query, out, reg, kind)
        return out, [f"[engine] datafusion", f"[sql] {sql}"], "A/B", ar
    # C 类规则排行：DataFusion 难直译，fallback pandas
    return _pandas_run_query(df, query, reg)


# ----------------------------- DSL → SQL -----------------------------
_OPMAP = {">=": ">=", "<=": "<=", "=": "=", "!=": "<>", ">": ">", "<": "<"}


def _esc(s) -> str:
    return str(s).replace("'", "''")


def _lit(v) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + _esc(v) + "'"


def _agg_sql(agg: str) -> str:
    return {"count": "COUNT", "avg": "AVG", "mean": "AVG",
            "sum": "SUM", "min": "MIN", "max": "MAX"}.get(agg, "COUNT")


def _filter_sql(f: dict) -> str | None:
    fld = f'"{f["field"]}"'   # field 已展开为物理名
    op = f["op"]; val = f.get("value")
    # 时间谓词需解析中文营业时间文本，DataFusion 难直译 → 降级 pandas
    if op in ("close_after", "open_after"):
        return None
    if op == "in":
        items = val if isinstance(val, (list, tuple, set)) else [val]
        return f"{fld} IN ({', '.join(_lit(x) for x in items)})"
    if op == "contains":
        return f"{fld} LIKE '%{_esc(val)}%'"
    if op == "not_contains":
        return f"{fld} NOT LIKE '%{_esc(val)}%'"
    if op == "is_null":
        return f"{fld} IS NULL"
    if op == "not_null":
        return f"{fld} IS NOT NULL"
    if op == "between":
        return f"{fld} BETWEEN {_lit(val[0])} AND {_lit(val[1])}"
    if op == "like":
        return f"{fld} LIKE '{_esc(val)}'"
    if op in _OPMAP:
        return f"{fld} {_OPMAP[op]} {_lit(val)}"
    # regex / 其它 DataFusion 难直译 → 返回 None 触发降级
    return None


def _dsl_to_sql(q: dict, reg) -> str | None:
    group_by = [reg.resolve(c) for c in (q.get("group_by") or [])]
    metrics = q.get("metrics")
    if group_by and not metrics:
        metrics = [{"name": "count", "agg": "count"}]
    select = q.get("select")
    filters = q.get("filters") or []
    order_by = q.get("order_by") or []
    limit = q.get("limit")

    cols: list[str] = []
    if group_by:
        cols += [f'"{c}"' for c in group_by]
    if metrics:
        for m in metrics:
            agg = m.get("agg", "count")
            if agg == "count":
                # 与 pandas FilterQuery：count 用 size() 计组内总行数（含 null）
                alias = m.get("name") or f"{m.get('field', '_')}_{agg}"
                cols.append(f'COUNT(*) AS "{alias}"')
                continue
            fname = reg.resolve(m["field"])
            alias = m.get("name") or f"{m.get('field', '_')}_{agg}"
            cols.append(f'{_agg_sql(agg)}("{fname}") AS "{alias}"')
    if not cols:
        cols = [f'"{reg.resolve(c)}"' for c in select] if select else ["*"]

    sql = "SELECT " + ", ".join(cols) + " FROM sh"
    wheres = [_filter_sql(f) for f in filters]
    if any(w is None for w in wheres):
        return None   # 含不支持算子 → 降级
    wheres = [w for w in wheres if w]
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    if group_by:
        sql += " GROUP BY " + ", ".join(f'"{c}"' for c in group_by)
    if order_by:
        ob = ", ".join(
            f'"{reg.resolve(o["field"])}" {"DESC" if o.get("dir", "desc").lower() == "desc" else "ASC"}'
            for o in order_by)
        sql += " ORDER BY " + ob
    elif group_by and metrics:
        # 与 pandas FilterQuery.run 对齐：B 类默认按主指标降序
        primary = metrics[0].get("name") or f"{metrics[0].get('field', '_')}_{metrics[0].get('agg', 'count')}"
        sql += f' ORDER BY "{primary}" DESC'
    if limit:
        sql += f" LIMIT {int(limit)}"
    return sql
