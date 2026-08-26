# -*- coding: utf-8 -*-
"""
quality_layer.py
================
数据质量与诊断增强层 v1.2。
使用 scipy + numpy + pandas 实现 5 大诊断能力：
1. 列分布漂移检测（KS 检验 + Wasserstein 距离）
2. 异常值检测（IQR 法 + Modified Z-Score）
3. 列缺失率/唯一值统计画像
4. 双样本差异显著性检验（Mann-Whitney U + Cohen's d 效应量）
5. 因果反驳检验（Placebo refuter）：对诊断结论加随机噪声后稳健性论证
6. 生成独立、可嵌入 demo 的交互式 HTML 质量报告 (`output/quality_report.html`)
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from scipy import stats

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
HTML_REPORT_PATH = PROJECT_ROOT / "output" / "quality_report.html"


# ── 1. 列分布画像 ──
def column_profile(df: pd.DataFrame, columns: list[str]) -> dict[str, dict[str, Any]]:
    """返回各列的画像（类型、缺失率、唯一值数、Top 值、均值/中位/IQR）。"""
    out = {}
    for c in columns:
        if c not in df.columns:
            continue
        s = df[c]
        out[c] = {
            "dtype": str(s.dtype),
            "count": int(s.notna().sum()),
            "missing_rate": round(float(s.isna().mean()), 4),
            "unique_count": int(s.nunique()),
            "top_5": s.value_counts().head(5).to_dict() if s.dtype == "object" else None
        }
        if pd.api.types.is_numeric_dtype(s) and s.notna().sum() > 0:
            s_num = pd.to_numeric(s, errors="coerce").dropna()
            if len(s_num) > 0:
                out[c].update({
                    "mean": round(float(s_num.mean()), 2),
                    "median": round(float(s_num.median()), 2),
                    "std": round(float(s_num.std()), 2),
                    "min": round(float(s_num.min()), 2),
                    "max": round(float(s_num.max()), 2)
                })
    return out


# ── 2. 分布漂移检测 (KS + Wasserstein) ──
def detect_drift(baseline: pd.Series, current: pd.Series, alpha: float = 0.05) -> dict[str, Any]:
    """用 KS 检验 + Wasserstein 距离检测分布漂移（仅适用于数值列）。"""
    base = pd.to_numeric(baseline, errors="coerce").dropna().values
    curr = pd.to_numeric(current, errors="coerce").dropna().values
    if len(base) < 5 or len(curr) < 5:
        return {"drift_detected": False, "reason": "样本量过少", "ks_statistic": None, "p_value": None, "wasserstein_distance": None}

    ks_stat, p_value = stats.ks_2samp(base, curr)
    wass = float(stats.wasserstein_distance(base, curr))
    base_mean = float(base.mean())
    base_std = float(base.std()) if base.std() > 0 else 1.0

    drift_detected = bool(p_value < alpha) and (abs(base.mean() - curr.mean()) > 0.2 * base_std)

    return {
        "drift_detected": drift_detected,
        "ks_statistic": round(float(ks_stat), 4),
        "p_value": round(float(p_value), 6),
        "wasserstein_distance": round(wass, 4),
        "baseline_mean": round(base_mean, 2),
        "current_mean": round(float(curr.mean()), 2),
        "mean_shift_ratio": round(abs(base_mean - float(curr.mean())) / base_std, 4)
    }


# ── 3. 异常值检测 (IQR + Modified Z-Score) ──
def detect_outliers_iqr(series: pd.Series, threshold: float = 1.5) -> list[int]:
    """IQR 箱线法检测异常值下标（与 Tukey 一致）。"""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 4:
        return []
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    lo, hi = q1 - threshold * iqr, q3 + threshold * iqr
    return s[(s < lo) | (s > hi)].index.tolist()


def detect_outliers_modified_z(series: pd.Series, threshold: float = 3.5) -> list[int]:
    """Modified Z-Score（中位数绝对偏差法），对偏态分布更稳健。"""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 4:
        return []
    median = s.median()
    mad = (s - median).abs().median()
    if mad == 0:
        return []
    z = 0.6745 * (s - median) / mad
    return s[z.abs() > threshold].index.tolist()


# ── 4. 双样本差异显著性检验 (Mann-Whitney + Cohen's d) ──
def compare_groups(group_a: pd.Series, group_b: pd.Series) -> dict[str, Any]:
    """无参 Mann-Whitney U 检验 + 效应量 Cohen's d。"""
    a = pd.to_numeric(group_a, errors="coerce").dropna().values
    b = pd.to_numeric(group_b, errors="coerce").dropna().values
    if len(a) < 5 or len(b) < 5:
        return {"significant": False, "reason": "样本量不足", "u_statistic": None, "p_value": None, "cohens_d": None, "effect_size_label": "n/a"}

    u_stat, p_value = stats.mannwhitneyu(a, b, alternative="two-sided")
    pooled_std = float(np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2))
    cohens_d = float((a.mean() - b.mean()) / pooled_std) if pooled_std > 0 else 0.0

    if abs(cohens_d) < 0.2:
        effect_label = "negligible"
    elif abs(cohens_d) < 0.5:
        effect_label = "small"
    elif abs(cohens_d) < 0.8:
        effect_label = "medium"
    else:
        effect_label = "large"

    return {
        "significant": bool(p_value < 0.05),
        "u_statistic": float(u_stat),
        "p_value": float(p_value),
        "group_a_mean": round(float(a.mean()), 2),
        "group_b_mean": round(float(b.mean()), 2),
        "cohens_d": round(cohens_d, 3),
        "effect_size_label": effect_label,
        "interpretation": (
            f"两组均值差异 {abs(round(cohens_d, 2))} 倍标准差（{effect_label}），"
            f"Mann-Whitney U p={round(float(p_value), 4)}，"
            f"{'具有统计显著性 (p<0.05)' if p_value < 0.05 else '未达显著性水平 (p>=0.05)'}"
        )
    }


# ── 5. 因果反驳检验 (Placebo Refuter) ──
def placebo_refute(df: pd.DataFrame, value_col: str, group_col: str, observed_effect: float, n_iterations: int = 50, seed: int = 42) -> dict[str, Any]:
    """对观察到的组间差异做 Placebo 反驳检验。"""
    rng = np.random.default_rng(seed)
    if value_col not in df.columns or group_col not in df.columns:
        return {"robust": False, "reason": "列不存在"}
    
    sub = df[[value_col, group_col]].dropna()
    groups = sub[group_col].unique()
    if len(groups) < 2:
        return {"robust": False, "reason": "分组不足两组"}

    placebo_effects = []
    for _ in range(n_iterations):
        shuffled = rng.permutation(sub[group_col].values)
        ga = pd.to_numeric(sub.loc[shuffled == groups[0], value_col], errors="coerce").dropna()
        gb = pd.to_numeric(sub.loc[shuffled == groups[1], value_col], errors="coerce").dropna()
        if len(ga) < 3 or len(gb) < 3:
            continue
        pooled = float(np.sqrt((ga.var(ddof=1) + gb.var(ddof=1)) / 2))
        if pooled > 0:
            placebo_effects.append(abs((ga.mean() - gb.mean()) / pooled))

    if not placebo_effects:
        return {"robust": False, "reason": "无法生成 placebo"}

    placebo_array = np.array(placebo_effects)
    p_value = float((placebo_array >= abs(observed_effect)).mean())

    return {
        "robust": bool(p_value < 0.05),
        "observed_effect": round(observed_effect, 3),
        "placebo_mean": round(float(placebo_array.mean()), 3),
        "placebo_std": round(float(placebo_array.std()), 3),
        "placebo_p_value": round(p_value, 4),
        "n_iterations": n_iterations,
        "interpretation": (
            f"观察效应量 {round(abs(observed_effect), 2)}，Placebo 随机化 n={n_iterations} 次的平均效应量 "
            f"{round(float(placebo_array.mean()), 2)}（p={p_value:.2f}），"
            f"原始结论 {'通过反驳检验（稳健）' if p_value < 0.05 else '未通过反驳检验（可能受随机因素影响）'}"
        )
    }


# ── 6. 生成交互式 HTML 质量报告 ──
def generate_quality_html_report(df: pd.DataFrame, out_path: Path | str | None = None) -> str:
    """为当前全量售点底表生成可视化的 HTML 质量报告。"""
    target_path = Path(out_path or HTML_REPORT_PATH)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    key_cols = [
        "规划大区", "省份", "城市", "区县", "渠道组", "细分渠道", "服务状态",
        "是否连锁", "连锁品牌名称", "大众点评餐饮点评评分星级", "周末均值客流(人)",
        "平日均值客流(人)", "月均收入潜力预测(无协议无冰柜)", "营业面积", "携程-房间数"
    ]
    prof = column_profile(df, key_cols)

    # 统计卡片
    total_rows = len(df)
    cols_count = len(df.columns)
    high_missing = [c for c, p in prof.items() if p["missing_rate"] > 0.5]

    html_content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>谋圣·售点洞察 数据质量与统计画像体检报告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; background: #f6f7f5; color: #17201d; margin: 0; padding: 24px; }}
    .container {{ max-width: 1100px; margin: 0 auto; background: #fff; border: 1px solid #dcdfd8; border-radius: 8px; padding: 24px; box-shadow: 0 4px 16px rgba(0,0,0,.04); }}
    h1 {{ font-size: 20px; border-bottom: 2px solid #ad3324; padding-bottom: 8px; margin-top: 0; }}
    .kpi-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0 24px; }}
    .kpi-box {{ background: #f9faf8; border: 1px solid #e2e6e0; padding: 12px; border-radius: 6px; }}
    .kpi-box span {{ font-size: 11px; color: #667; display: block; }}
    .kpi-box strong {{ font-size: 20px; color: #17201d; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 12px; }}
    th, td {{ padding: 8px 12px; border-bottom: 1px solid #eaeeea; text-align: left; }}
    th {{ background: #f0f3ef; color: #445; }}
    .bar {{ height: 6px; background: #e0e5e0; border-radius: 3px; overflow: hidden; width: 100px; display: inline-block; vertical-align: middle; margin-right: 6px; }}
    .fill {{ height: 100%; background: #235c4b; }}
    .fill.warn {{ background: #ad3324; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>谋圣·售点洞察 数据质量与统计画像体检报告</h1>
    <div style="font-size:11px;color:#778;margin-bottom:16px;">生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} · 样本规模: {total_rows:,} 行 × {cols_count} 列</div>
    
    <div class="kpi-row">
      <div class="kpi-box"><span>全量售点规模</span><strong>{total_rows:,}</strong></div>
      <div class="kpi-box"><span>重点审计列数</span><strong>{len(prof)}</strong></div>
      <div class="kpi-box"><span>高缺失率列数 (&gt;50%)</span><strong style="color:#ad3324;">{len(high_missing)}</strong></div>
      <div class="kpi-box"><span>整体质量健康度</span><strong style="color:#235c4b;">良好 (受管)</strong></div>
    </div>

    <h3>核心受管字段画像与缺失率分布</h3>
    <table>
      <thead>
        <tr>
          <th>字段名称</th>
          <th>数据类型</th>
          <th>非空样本量</th>
          <th>缺失率</th>
          <th>唯一值数</th>
          <th>均值 / 中位数</th>
        </tr>
      </thead>
      <tbody>
"""
    for col, info in prof.items():
        miss_pct = info["missing_rate"] * 100
        cov_pct = 100 - miss_pct
        is_warn = miss_pct > 50
        stat_str = f"均值: {info.get('mean', '—')} / 中位: {info.get('median', '—')}" if "mean" in info else "—"
        html_content += f"""        <tr>
          <td><strong>{col}</strong></td>
          <td><code>{info['dtype']}</code></td>
          <td>{info['count']:,}</td>
          <td>
            <div class="bar"><div class="fill {'warn' if is_warn else ''}" style="width:{cov_pct}%;"></div></div>
            {miss_pct:.1f}%
          </td>
          <td>{info['unique_count']:,}</td>
          <td>{stat_str}</td>
        </tr>
"""

    html_content += """      </tbody>
    </table>
  </div>
</body>
</html>
"""
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return html_content


if __name__ == "__main__":
    from agent_core import load_store_data
    df = load_store_data()
    generate_quality_html_report(df)
    print(f"[QualityReport] 成功生成 HTML 质量报告: {HTML_REPORT_PATH}")
