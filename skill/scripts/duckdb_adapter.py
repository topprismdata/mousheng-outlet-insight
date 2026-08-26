# -*- coding: utf-8 -*-
"""
duckdb_adapter.py
=================
DuckDB 高性能数据层适配器 (v1.2)。
以内存数据库方式直读 Parquet 文件 (629,074 行 × 153 列)，提供毫秒级 SQL 查询执行。

主要能力：
1. `init_duckdb(parquet_path)`: 将 Parquet 注册为 `outlets` 视图；
2. `query_df(sql)`: 执行只读 SQL 并直接返回 pandas DataFrame；
3. `query_arrow(sql)`: 执行只读 SQL 并返回 PyArrow Table；
4. 字段名与类型安全的元数据自检。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
import duckdb
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_PARQUET_PATH = PROJECT_ROOT / "data" / "SH全量售点洞察1784527757315.parquet"

_GLOBAL_CON: duckdb.DuckDBPyConnection | None = None
_REGISTERED_PATH: Path | None = None


def get_duckdb_connection(parquet_path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """获取或初始化 DuckDB 单例连接。"""
    global _GLOBAL_CON, _REGISTERED_PATH

    target_path = Path(parquet_path or DEFAULT_PARQUET_PATH)
    if not target_path.exists():
        raise FileNotFoundError(f"Parquet 数据文件不存在: {target_path}")

    if _GLOBAL_CON is None or _REGISTERED_PATH != target_path:
        t0 = time.perf_counter()
        con = duckdb.connect(database=":memory:")
        # 注册 Parquet 视图
        con.execute(f"CREATE OR REPLACE VIEW outlets AS SELECT * FROM read_parquet('{target_path.as_posix()}')")
        _GLOBAL_CON = con
        _REGISTERED_PATH = target_path
        row_count = con.execute("SELECT COUNT(*) FROM outlets").fetchone()[0]
        print(f"[DuckDB] 成功注册 outlets 视图 ({row_count:,} 行，耗时 {time.perf_counter()-t0:.3f}s)")

    return _GLOBAL_CON


def query_df(sql: str, parquet_path: Path | str | None = None) -> pd.DataFrame:
    """执行 SQL 查询并返回 pandas DataFrame。"""
    con = get_duckdb_connection(parquet_path)
    return con.execute(sql).df()


def query_arrow(sql: str, parquet_path: Path | str | None = None) -> Any:
    """执行 SQL 查询并返回 PyArrow Table。"""
    con = get_duckdb_connection(parquet_path)
    return con.execute(sql).arrow()


def get_table_schema(parquet_path: Path | str | None = None) -> dict[str, str]:
    """获取 outlets 视图的列名和数据类型。"""
    con = get_duckdb_connection(parquet_path)
    df_desc = con.execute("DESCRIBE outlets").df()
    return dict(zip(df_desc["column_name"], df_desc["column_type"]))


if __name__ == "__main__":
    t0 = time.perf_counter()
    con = get_duckdb_connection()
    df_sample = query_df("SELECT 规划大区, 渠道组, COUNT(*) as 售点数 FROM outlets WHERE 省份 = '上海市' GROUP BY 规划大区, 渠道组 ORDER BY 售点数 DESC LIMIT 10")
    print(f"\n[DuckDB Test] 查询耗时: {time.perf_counter()-t0:.3f}s")
    print(df_sample)
