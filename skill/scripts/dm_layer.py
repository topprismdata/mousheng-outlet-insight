#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DM 汇总层（Data Mart）— 第 10 块能力：交互式统计查询 + 下钻

设计目标
--------
把"即席扫描 629K 行做 group_by"变成"命中预物化汇总表（毫秒级）+ 客户点选下钻原始明细"。

核心组件
--------
1. build_dm(parquet, db)        : 扫一遍底表，物化多张低粒度汇总表到 SQLite
2. query_stat(db, spec)         : 统计级别查询，命中 DM（不扫底表）
3. drilldown(parquet, filters)  : 给定下钻过滤键，回底表取原始明细

DM 表设计（grain = 维度组合，measures = 预聚合指标）
---------------------------------------------------
dm_brand_channel : grain(省份,城市,规划大区,渠道组,细分渠道,是否连锁,连锁品牌名称)
                  → cnt, avg_rating, avg_traffic, sum_potential
                  覆盖：品牌排行 / 业态分布 / 子渠道分布 / 连锁 vs 非连锁
dm_region        : grain(省份,城市,规划大区,渠道组) → cnt
                  覆盖：区域 × 业态 分布

下钻键（drill_key）：每条 DM 汇总行携带其维度取值，客户点选后直接拼成底表过滤条件。

零 LLM 依赖，纯 pandas + sqlite3。
"""

import sqlite3
import os
import time
from pathlib import Path

import pandas as pd
import numpy as np

# ---- 维度 / 度量定义 -------------------------------------------------------
DIMENSIONS = ["省份", "城市", "规划大区", "渠道组", "细分渠道", "是否连锁", "连锁品牌名称"]

# 地理归一化：用户口语（省级/简称）→ 底表 城市 列真实取值
# 生产环境应下沉到 field_registry 语义层，这里先做最小可用映射让示例跑通
GEO_NORM = {
    "目标城市": "目标城区",
    "目标城市": "目标城区",
    "目标城市1": "目标城市1市",
    "目标城市5": "目标城市5市",
    "目标城市8": "目标城市8市",
    "目标城市7": "目标城市7市",
}

MEASURE_COLS = {
    "avg_rating": "大众点评餐饮点评评分星级",
    "avg_traffic": "周末均值客流(人)",
    "sum_potential": "月均收入潜力预测(如有协议)",
}


def _coerce_numeric(df: pd.DataFrame):
    """把度量列转成 numeric，失败填 NaN。"""
    for dst, src in MEASURE_COLS.items():
        if src in df.columns:
            df[dst] = pd.to_numeric(df[src], errors="coerce")
    return df


def build_dm(parquet_path: str, db_path: str, verbose: bool = True):
    """
    扫一遍底表，物化 DM 汇总表到 SQLite。

    parquet_path : 底表 parquet（首次读约 1-2s，之后缓存）
    db_path      : SQLite 输出路径（已存在则重建）
    """
    t0 = time.time()
    if verbose:
        print(f"[DM] 读取底表: {parquet_path}")
    df = pd.read_parquet(parquet_path, engine="pyarrow")
    if verbose:
        print(f"[DM] 底表 {len(df):,} 行 × {len(df.columns)} 列，"
              f"载入 {time.time()-t0:.1f}s")

    df = _coerce_numeric(df)
    # 品牌 NaN → 非连锁占位（便于统计）
    df["连锁品牌名称"] = df["连锁品牌名称"].fillna("(非连锁/无名)")

    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)

    # ---- 表 1: dm_brand_channel -------------------------------------------
    grp = df.groupby(DIMENSIONS, dropna=False)
    agg = grp.agg(
        cnt=("腾讯售点id", "count"),
        avg_rating=("avg_rating", "mean"),
        avg_traffic=("avg_traffic", "mean"),
        sum_potential=("sum_potential", "sum"),
    ).reset_index()
    agg.to_sql("dm_brand_channel", conn, index=False, if_exists="replace")
    if verbose:
        print(f"[DM] dm_brand_channel: {len(agg):,} 行（grain={len(DIMENSIONS)}维）")

    # ---- 表 2: dm_region --------------------------------------------------
    reg_dims = ["省份", "城市", "规划大区", "渠道组"]
    reg = df.groupby(reg_dims, dropna=False).agg(
        cnt=("腾讯售点id", "count"),
    ).reset_index()
    reg.to_sql("dm_region", conn, index=False, if_exists="replace")
    if verbose:
        print(f"[DM] dm_region: {len(reg):,} 行")

    # ---- 索引（加速过滤/排序）--------------------------------------------
    conn.execute("CREATE INDEX idx_bc_city ON dm_brand_channel(城市)")
    conn.execute("CREATE INDEX idx_bc_channel ON dm_brand_channel(渠道组)")
    conn.execute("CREATE INDEX idx_bc_chain ON dm_brand_channel(是否连锁)")
    conn.execute("CREATE INDEX idx_bc_brand ON dm_brand_channel(连锁品牌名称)")
    conn.commit()
    conn.close()

    if verbose:
        size = os.path.getsize(db_path) / 1024 / 1024
        print(f"[DM] 完成，SQLite {size:.1f}MB，总耗时 {time.time()-t0:.1f}s")
    return db_path


def query_stat(db_path: str,
               group_by: str,
               filters: dict = None,
               order_by: str = "cnt",
               ascending: bool = False,
               limit: int = None):
    """
    统计级别查询，命中 DM 表（不扫底表）。

    group_by : 需是 DM 维度之一（省份/城市/规划大区/渠道组/细分渠道/是否连锁/连锁品牌名称）
    filters  : {维度: 值} 等值过滤（如 {"城市":"目标城市","渠道组":"餐饮","是否连锁":"是"}）
    order_by : "cnt" / "avg_rating" / "avg_traffic" / "sum_potential"
    returns  : (DataFrame, drill_note)
    """
    conn = sqlite3.connect(db_path)
    # 选表：group_by 命中 region 维度 + 不需品牌 + 仅按 cnt 排 → dm_region（更轻）；
    # 但若要按 avg_rating/avg_traffic/sum_potential 排序/聚合，dm_region 无这些列，必须退回 dm_brand_channel。
    need_full = order_by in ("avg_rating", "avg_traffic", "sum_potential")
    if (not need_full and group_by in ("省份", "规划大区")
            and (filters is None or "连锁品牌名称" not in (filters or {}))):
        table = "dm_region"
        avail = ["省份", "城市", "规划大区", "渠道组"]
    else:
        table = "dm_brand_channel"
        avail = DIMENSIONS

    if group_by not in avail:
        conn.close()
        raise ValueError(f"group_by={group_by} 不在 DM 维度 {avail} 内")

    where = []
    params = []
    ignored: list[str] = []
    if filters:
        for k, v in filters.items():
            if k not in avail:
                # 不静默丢弃（Q-province 静默忽略教训）：记入 drill_note.ignored
                ignored.append(k)
                continue
            # 地理归一化：目标城市 → 目标城区
            if k == "城市" and v in GEO_NORM:
                v = GEO_NORM[v]
            where.append(f'"{k}" = ?')
            params.append(v)

    sql = f'SELECT "{group_by}" AS dim_value, SUM(cnt) AS cnt'
    # 仅当 group_by 不是聚合维度时，附带平均值类度量（按 cnt 加权）
    if table == "dm_brand_channel":
        sql += ", SUM(avg_rating*cnt)/SUM(cnt) AS avg_rating"
        sql += ", SUM(avg_traffic*cnt)/SUM(cnt) AS avg_traffic"
        sql += ", SUM(sum_potential) AS sum_potential"
    sql += f' FROM {table}'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f' GROUP BY "{group_by}"'
    sql += f' ORDER BY {order_by} {"ASC" if ascending else "DESC"}'
    if limit:
        sql += f" LIMIT {int(limit)}"

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()

    # 下钻提示：返回该行可作为下钻过滤键的维度；ignored = 未被 DM 支持而忽略的过滤维度
    drill_note = {"group_by": group_by, "filters": filters or {}, "ignored": ignored}
    return df, drill_note


def query_stat_cross(db_path: str,
                     group_by: str,
                     z: str,
                     filters: dict = None,
                     measure: str = "cnt",
                     ascending: bool = False,
                     limit: int = 20):
    """
    二维统计聚合（group_by × z），命中 DM 表，用于「多系列 / 分组着色」图形化。

    - group_by / z 须都是 DM 维度之一，且不能相同。
    - 自动选表：两维都在 省份/城市/规划大区/渠道组 且 measure=cnt → dm_region；
      否则 dm_brand_channel（含全部度量）。
    - avg_* 度量按 cnt 加权均值计算（cell 级正确；分组 top-N 也用加权均值，不做静默求和）。
    - 返回 top-N 主维度分组，并保留其全部 z 细分（分组柱状图/折线常用）。
    returns: (DataFrame[dim_value, z_value, cnt, avg_rating, avg_traffic, sum_potential], drill_note)
    """
    if group_by not in DIMENSIONS or z not in DIMENSIONS:
        raise ValueError(f"group_by/z 须是 DM 维度: {DIMENSIONS}")
    if group_by == z:
        raise ValueError("group_by 与 z 不能相同（二维聚合需两个不同维度）")

    reg_dims = ["省份", "城市", "规划大区", "渠道组"]
    use_region = (group_by in reg_dims and z in reg_dims and measure == "cnt")
    table = "dm_region" if use_region else "dm_brand_channel"
    avail = reg_dims if use_region else DIMENSIONS

    conn = sqlite3.connect(db_path)
    where, params, ignored = [], [], []
    if filters:
        for k, v in filters.items():
            if k not in avail:
                ignored.append(k)  # 不静默丢弃，记入 drill_note.ignored
                continue
            if k == "城市" and v in GEO_NORM:
                v = GEO_NORM[v]
            where.append(f'"{k}" = ?')
            params.append(v)

    # 始终带 cnt；dm_brand_channel 额外带全部度量（dm_region 仅有 cnt，不能 SELECT 不存在的列）
    if table == "dm_region":
        sql = (
            f'SELECT "{group_by}" AS dim_value, "{z}" AS z_value, SUM(cnt) AS cnt '
            f'FROM {table}'
        )
    else:
        sql = (
            f'SELECT "{group_by}" AS dim_value, "{z}" AS z_value, SUM(cnt) AS cnt, '
            f'SUM(avg_rating*cnt)/SUM(cnt) AS avg_rating, '
            f'SUM(avg_traffic*cnt)/SUM(cnt) AS avg_traffic, '
            f'SUM(sum_potential) AS sum_potential '
            f'FROM {table}'
        )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f' GROUP BY "{group_by}", "{z}"'
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()

    if df.empty:
        drill_note = {"group_by": group_by, "z": z, "measure": measure,
                      "filters": filters or {}, "ignored": ignored}
        return df, drill_note

    # top-N 主维度分组：avg 度量用加权均值排名，cnt/sum 用求和
    if measure in ("avg_rating", "avg_traffic"):
        # 组内加权均值 = Σ(avg*cnt) / Σ(cnt)，避免 apply(include_groups) 的版本依赖
        w = df.assign(_w=df[measure] * df["cnt"]).groupby("dim_value").agg(
            _num=("_w", "sum"), _den=("cnt", "sum"))
        tot = (w["_num"] / w["_den"]).reset_index(name=measure)
    else:
        tot = df.groupby("dim_value", as_index=False)[measure].sum()
    tot = tot.sort_values(measure, ascending=ascending)
    top = set(tot["dim_value"].head(limit).tolist())
    df = df[df["dim_value"].isin(top)].copy()
    df = df.sort_values(["dim_value", measure], ascending=[True, ascending]).reset_index(drop=True)

    drill_note = {"group_by": group_by, "z": z, "measure": measure,
                  "filters": filters or {}, "ignored": ignored}
    return df, drill_note


def drilldown(parquet_path: str, filters: dict, limit: int = 200):
    """
    下钻：给定过滤键，回底表取原始明细。

    filters : {维度: 值} 精确过滤（如 城市=目标城市, 渠道组=餐饮, 是否连锁=是, 连锁品牌名称=肯德基）
    returns : DataFrame（原始行，限量）
    """
    df = pd.read_parquet(parquet_path, engine="pyarrow")
    mask = pd.Series(True, index=df.index)
    for k, v in filters.items():
        if k not in df.columns:
            continue
        if k == "城市" and v in GEO_NORM:
            v = GEO_NORM[v]
        if v == "(非连锁/无名)":
            mask &= df[k].isna()
        else:
            mask &= (df[k].astype(str) == str(v))
    sub = df[mask].head(limit)
    return sub


def drilldown_df(df: pd.DataFrame, filters: dict, limit: int = 200) -> pd.DataFrame:
    """下钻（内存版）：底表已常驻内存时（如 MCP 进程）直接过滤，不再读 parquet。"""
    mask = pd.Series(True, index=df.index)
    for k, v in filters.items():
        if k not in df.columns:
            continue
        if k == "城市" and v in GEO_NORM:
            v = GEO_NORM[v]
        if v == "(非连锁/无名)":
            mask &= df[k].isna()
        else:
            mask &= (df[k].astype(str) == str(v))
    return df[mask].head(limit)


# ---------------------------------------------------------------------------
# DM 自动发现 / 自愈构建 / B 类查询路由（接入 agent_core 执行路径）
# ---------------------------------------------------------------------------
def default_dm_path(data_path: str | Path) -> Path:
    """DM SQLite 默认放在数据文件同目录：<data>.dm.sqlite（跟随数据走，天然隔离多份数据）。"""
    p = Path(data_path)
    return p.with_suffix(".dm.sqlite")


def ensure_dm(data_path: str | Path, db_path: str | Path | None = None,
              verbose: bool = False) -> str | None:
    """确保 DM 存在且比数据新；不存在/过期则自动重建。失败返回 None（调用方回退底表扫描）。"""
    data_path = Path(data_path)
    pq = data_path.with_suffix(".parquet")
    src = pq if pq.exists() else data_path
    if not src.exists():
        return None
    db = Path(db_path) if db_path else default_dm_path(data_path)
    try:
        if db.exists() and db.stat().st_mtime >= src.stat().st_mtime:
            return str(db)
        build_dm(str(src), str(db), verbose=verbose)
        return str(db)
    except Exception as e:
        if verbose:
            print(f"[DM] 构建失败，回退底表扫描: {e}")
        return None


# B 类 DSL → DM 可路由的条件：
#   - group_by 恰好 1 个维度，且 ∈ DIMENSIONS
#   - filters 全部是 DM 维度上的等值过滤（=/==/eq）
#   - metrics 为空或 count（DM 附带 avg_rating/avg_traffic/sum_potential 加权值）
_EQ_OPS = {"=", "==", "eq"}
_ORDER_MAP = {  # DSL 排序字段 → DM 度量列
    "count": "cnt", "cnt": "cnt", "门店数": "cnt",
    "大众点评餐饮点评评分星级": "avg_rating",
    "周末均值客流(人)": "avg_traffic",
    "月均收入潜力预测(如有协议)": "sum_potential",
}


def route_b_query(query: dict, reg=None, db_path: str | None = None):
    """尝试把 A/B DSL 路由到 DM 汇总表执行。

    可路由 → 返回 (out_df, trace_list)；不可路由 → 返回 None（调用方走底表扫描）。
    reg 传入 FieldRegistry 时会先 resolve 别名到物理字段名。
    """
    if not db_path or not os.path.exists(db_path):
        return None
    gb = query.get("group_by") or []
    if len(gb) != 1:
        return None
    _r = (lambda n: reg.resolve(n)) if reg is not None else (lambda n: n)
    gb_col = _r(gb[0])
    if gb_col not in DIMENSIONS:
        return None
    # metrics: 只有 count 可路由（其余聚合口径需回底表）
    metrics = query.get("metrics") or [{"name": "count", "agg": "count"}]
    if len(metrics) != 1 or metrics[0].get("agg", "count") != "count":
        return None
    # filters: 全部须是 DM 维度等值
    dm_filters: dict = {}
    for f in query.get("filters") or []:
        col = _r(f.get("field", ""))
        if col not in DIMENSIONS or f.get("op") not in _EQ_OPS:
            return None
        dm_filters[col] = f.get("value")
    # order_by: 缺省按 cnt；显式排序字段必须能映射到 DM 度量
    order_col, asc = "cnt", False
    ob = query.get("order_by") or []
    if ob:
        if len(ob) > 1:
            return None
        mapped = _ORDER_MAP.get(_r(ob[0].get("field", "")))
        if mapped is None:
            return None
        order_col = mapped
        asc = ob[0].get("dir", "desc").lower() == "asc"

    try:
        out, note = query_stat(db_path, group_by=gb_col, filters=dm_filters,
                               order_by=order_col, ascending=asc,
                               limit=query.get("limit"))
    except Exception:
        return None
    if note.get("ignored"):
        return None  # 有过滤器无法在 DM 表达 → 拒绝路由，避免静默丢条件
    out = out.rename(columns={"dim_value": gb_col,
                              "cnt": metrics[0].get("name") or "count"})
    trace = [f"[DM] 命中汇总表 dm_layer（毫秒级，未扫底表）",
             f"[DM] group_by={gb_col} filters={dm_filters} order={order_col} "
             f"{'asc' if asc else 'desc'} limit={query.get('limit')}",
             f"[DM] 下钻键模板: {{**{dm_filters}, '{gb_col}': <点选值>}}"]
    return out, trace


# ---------------------------------------------------------------------------
# CLI 演示
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    PARQUET = "/Users/ghb/outlet-insight/data/<data>.parquet"
    DB = "/Users/ghb/outlet-insight/data/sh_dm.sqlite"

    if "--build" in sys.argv or not os.path.exists(DB):
        build_dm(PARQUET, DB)

    # 示例：目标城市餐饮的连锁品牌都有哪些，按照门店数量排序
    res, note = query_stat(
        DB,
        group_by="连锁品牌名称",
        filters={"城市": "目标城市", "渠道组": "餐饮", "是否连锁": "是"},
        order_by="cnt",
        limit=15,
    )
    pd.set_option("display.max_rows", 30)
    pd.set_option("display.width", 120)
    print("\n=== 统计查询：目标城市餐饮连锁品牌（按门店数排序 TOP15）===")
    print(res.to_string(index=False))

    if "--drill" in sys.argv and len(res) > 0:
        top_brand = res.iloc[0]["dim_value"]
        print(f"\n=== 下钻：目标城市餐饮 连锁品牌={top_brand} 的原始明细（前 5 行）===")
        detail = drilldown(PARQUET, {
            "城市": "目标城市", "渠道组": "餐饮",
            "是否连锁": "是", "连锁品牌名称": top_brand,
        }, limit=5)
        print(detail[["腾讯售点名称", "城市", "渠道组", "连锁品牌名称",
                      "大众点评餐饮点评评分星级", "周末均值客流(人)"]].to_string(index=False))
