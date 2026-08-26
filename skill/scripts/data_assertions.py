# -*- coding: utf-8 -*-
"""
data_assertions.py
===================
FR-39 数据质量断言（Denial Constraints / 数据漂移第一道防线）

基于**数据轮廓比对（Data Profiles Diffs）**：扫描输入快照，针对数值/类别列生成
统计轮廓（均值/标准差/空值率/值域），据此派生**拒绝约束（Denial Constraint）**；
新快照违反既有约束即**告警（不静默、不阻断）**，构成防止数据漂移（Data Drift）
导致管道崩溃的第一道防线。约束随数据演进**版本化**，纳入语义控制面治理。

设计要点（对齐 FR-39 验收标准 AC1~AC4）：
  - AC1 build_baseline 生成轮廓；generate_dcs 派生拒绝约束
  - AC2 check_dcs 违反即记录告警，绝不静默产错
  - AC3 baseline 带 version 指纹，约束可版本化
  - AC4 check_dcs 只返回告警列表，不修改/阻断输入

零 LLM、确定性、仅依赖 pandas（与全 Skill 栈一致）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class DenialConstraint:
    column: str
    kind: str            # "null_rate" | "mean_shift" | "std_shift" | "domain_drift"
    threshold: float
    baseline_value: Any  # 该列在基线处的参考统计量
    message: str


@dataclass
class Violation:
    column: str
    kind: str
    detail: str


# ----------------------------------------------------------------------- 基线轮廓
def build_baseline(df: pd.DataFrame, columns=None, domain_cap: int = 200) -> dict:
    """
    对真实快照生成统计轮廓基线。

    返回结构：
      { "version": <sha1 指纹前 12 位>,
        "n_rows": <行数>,
        "columns": { col: {dtype, null_rate, n_unique, numeric,
                            mean/std(数值) | domain/domain_capped(类别)} } }
    version 由「列名 + 关键统计量」取指纹，使约束可随数据演进版本化（AC3）。
    """
    cols = list(columns) if columns is not None else list(df.columns)
    profiles: dict[str, dict] = {}
    for c in cols:
        s = df[c]
        null_rate = float(s.isna().mean())
        n_unique = int(s.nunique(dropna=True))
        is_num = pd.api.types.is_numeric_dtype(s)
        prof: dict[str, Any] = {
            "name": c,
            "dtype": str(s.dtype),
            "null_rate": round(null_rate, 6),
            "n_unique": n_unique,
            "numeric": bool(is_num),
        }
        if is_num:
            prof["mean"] = float(s.mean())
            prof["std"] = float(s.std())
        else:
            vals = s.dropna().unique().tolist()
            capped = len(vals) > domain_cap
            prof["domain"] = [str(v) for v in vals[:domain_cap]]
            prof["domain_capped"] = capped
        profiles[c] = prof

    fp_src = json.dumps(profiles, sort_keys=True, ensure_ascii=False)
    version = hashlib.sha1(fp_src.encode("utf-8")).hexdigest()[:12]
    return {"version": version, "n_rows": int(len(df)), "columns": profiles}


# ----------------------------------------------------------------------- 拒绝约束派生
def generate_dcs(baseline: dict,
                 null_rate_tol: float = 0.10,
                 mean_z: float = 3.0,
                 std_ratio: float = 2.0) -> list[DenialConstraint]:
    """
    由基线派生拒绝约束（Denial Constraints）。

      - null_rate：空值率较基线上升超过容差即告警（捕获关键列空值率飙升）
      - mean_shift：数值均值偏离基线超过 ±mean_z·σ 即告警（捕获评分均值突变）
      - std_shift ：数值标准差较基线扩大超过 std_ratio× 即告警（捕获分布崩塌）
      - domain_drift：类别出现基线值域外的新值即告警（捕获口径/字典漂移）
    """
    dcs: list[DenialConstraint] = []
    for col, p in baseline["columns"].items():
        dcs.append(DenialConstraint(
            column=col, kind="null_rate", threshold=null_rate_tol,
            baseline_value=p["null_rate"],
            message=f"{col} 空值率较基线({p['null_rate']:.3f})上升超 {null_rate_tol:.0%} 即告警"))
        if p.get("numeric"):
            dcs.append(DenialConstraint(
                column=col, kind="mean_shift", threshold=mean_z,
                baseline_value=p["mean"],
                message=f"{col} 均值偏离基线(±{mean_z}σ)即告警"))
            dcs.append(DenialConstraint(
                column=col, kind="std_shift", threshold=std_ratio,
                baseline_value=p["std"],
                message=f"{col} 标准差较基线扩大超 {std_ratio}× 即告警"))
        elif not p.get("domain_capped", False):
            dom = set(p.get("domain", []))
            dcs.append(DenialConstraint(
                column=col, kind="domain_drift", threshold=0.0,
                baseline_value=sorted(dom),
                message=f"{col} 出现基线域外值(非已知 {len(dom)} 个值之一)即告警"))
    return dcs


# ----------------------------------------------------------------------- 新快照校验
def check_dcs(df: pd.DataFrame, dcs: list[DenialConstraint], baseline: dict) -> list[Violation]:
    """
    新快照对照拒绝约束：违反即记录告警。

    **绝不阻断、绝不静默**——只返回 Violation 列表（空=健康）。
    调用方据此决定告警升级，而非让管道静默产错。
    """
    cols = [d.column for d in dcs]
    now = build_baseline(df, columns=cols) if cols else {"columns": {}}
    viols: list[Violation] = []
    for dc in dcs:
        p = now["columns"].get(dc.column)
        if p is None:
            viols.append(Violation(dc.column, "missing_column", f"列 {dc.column} 在新快照缺失"))
            continue
        if dc.kind == "null_rate":
            if p["null_rate"] - dc.baseline_value > dc.threshold:
                viols.append(Violation(dc.column, "null_rate",
                    f"空值率 {p['null_rate']:.3f} 超基线 {dc.baseline_value:.3f}+容差{dc.threshold:.3f}"))
        elif dc.kind == "mean_shift" and p.get("numeric"):
            base_std = baseline["columns"][dc.column].get("std") or 1e-9
            if abs(p["mean"] - dc.baseline_value) > dc.threshold * base_std:
                viols.append(Violation(dc.column, "mean_shift",
                    f"均值 {p['mean']:.3f} 偏离基线 {dc.baseline_value:.3f} > {dc.threshold}σ"))
        elif dc.kind == "std_shift" and p.get("numeric"):
            if dc.baseline_value and p["std"] is not None and p["std"] > dc.threshold * dc.baseline_value:
                viols.append(Violation(dc.column, "std_shift",
                    f"标准差 {p['std']:.3f} > {dc.threshold}×基线 {dc.baseline_value:.3f}"))
        elif dc.kind == "domain_drift" and dc.baseline_value:
            new_vals = set(p.get("domain", [])) - set(dc.baseline_value)
            if new_vals:
                viols.append(Violation(dc.column, "domain_drift",
                    f"出现基线域外值: {sorted(new_vals)}"))
    return viols


# ----------------------------------------------------------------------- 便捷封装
def scan_drift(baseline_df: pd.DataFrame, new_df: pd.DataFrame,
               columns=None, **tol) -> list[Violation]:
    """一键：基线轮廓 → 派生约束 → 新快照校验，返回告警列表。"""
    baseline = build_baseline(baseline_df, columns=columns)
    dcs = generate_dcs(baseline, **tol)
    return check_dcs(new_df, dcs, baseline)
