# -*- coding: utf-8 -*-
"""
profiler.py
===========
售点洞察 — 字段统计画像层（第 5 块进阶能力：统计 / 画像层）。

解决的问题：解析器在「接到问题、真正跑之前」对数据长什么样几乎一无所知，
只能靠手写的 data_schema.md 与 registry 里写死的 values_sample。本模块把
「对字段做真实统计」变成可复用、可缓存的能力，让 Agent 拿到问题前就有方向：

  1) 每字段真实统计：基数 / Top 值 / 缺失率（类别·文本）；min~max/分位数（数值）；
     真/假计数（布尔）。
  2) 按业态（渠道组）拆分的覆盖率：跨业态稀疏字段（如点评评分只在餐饮有、携程字段
     只在住宿有）按业态给出覆盖率，供解析器「按业态路由字段」——这正是「方向感」的关键。
  3) 整体画像：规划大区 / 省份 / 城市 / 渠道组 / 细分渠道 / 经营场所 的分布。

产物缓存到数据同级的 `<数据名>.profile.json`（与 parquet 缓存同源、按 mtime 失效），
首次扫描约 15-30s，之后秒级加载。

下游消费方：
  - 解析器（nl_parser.route_metric）：模糊度量词「评分/客流/潜力…」按业态路由到正确字段。
  - LLM 兜底 prompt（_to_llm_prompt）：注入真实字段范围与 Top 值，降低 DSL 幻觉。
  - 会话开局定向 / MCP profile tool：Agent 随时查字段统计。
  - gen_schema_md：用真实数字反哺 data_schema.md，告别手写过期数字。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agent_core import FieldRegistry

# ---- DataSketches 懒加载（近似画像，超大数据/流式场景）----
_SKETCH_AVAIL = False
try:
    import datasketches as ds
    _SKETCH_AVAIL = True
except Exception:
    ds = None

# 跨业态维度：覆盖率随业态剧烈变化的字段，按此维度拆分布局
DEFAULT_业态_FIELD = "渠道组"
# 稀疏判定：整体覆盖率低于该阈值（即缺失率高于 1-该值）的字段视为「跨业态稀疏」
SPARSE_COVERAGE = 0.85

# 模糊度量词 → 候选字段（route_metric 路由用）。顺序即优先级（无画像时取首项）。
METRIC_TERM_FIELDS: dict[str, list[str]] = {
    "评分": ["大众点评餐饮点评评分星级", "携程-综合评分", "携程-星级"],
    "星级": ["携程-星级", "大众点评餐饮点评评分星级"],
    "客流": ["周末均值客流(人)", "平日均值客流(人)", "日均客流"],
    "潜力": ["月均收入潜力预测(无协议无冰柜)",
             "月均收入潜力预测(如有协议)",
             "月均收入潜力预测(如有冰柜无协议)"],
    "房间数": ["携程-房间数"],
    "客房数": ["携程-房间数"],
    "人均": ["大众点评餐饮人均消费"],
    "面积": ["营业面积"],
}
# 无画像时的静态兜底（保持现有解析行为，确保不回归）
STATIC_DEFAULT_FIELD: dict[str, str] = {
    "评分": "大众点评餐饮点评评分星级",
    "星级": "携程-星级",
    "客流": "周末均值客流(人)",
    "潜力": "月均收入潜力预测(无协议无冰柜)",
    "房间数": "携程-房间数",
    "客房数": "携程-房间数",
    "人均": "大众点评餐饮人均消费",
    "面积": "营业面积",
}

# 当前进程内画像（由 agent_cli / mcp_server 启动时 set_profile 注入；无则 route_metric 走静态兜底）
PROFILE: dict | None = None


# ----------------------------- 数值安全转换 -----------------------------
def _r(x):
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return None
        return round(float(x), 4)
    except Exception:
        return None


def _to_py(v):
    """numpy 类型转原生，保证 JSON 可序列化。"""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return _r(float(v))
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


# ----------------------------- 缓存路径 -----------------------------
def profile_cache_path(data_path: Path) -> Path:
    """画像缓存与 parquet 缓存同级：<数据名>.profile.json。"""
    return Path(data_path).with_suffix(".profile.json")


# ----------------------------- 构建画像 -----------------------------
def build_profile(df: pd.DataFrame, reg: FieldRegistry,
                  data_path: Path | None = None,
                  业态_field: str = DEFAULT_业态_FIELD,
                  approx: bool = False) -> dict:
    """扫描 DataFrame，构建字段统计画像。"""
    n = len(df)
    业态_vals = df[业态_field] if 业态_field in df.columns else None

    fields: dict[str, dict] = {}
    for fname, meta in reg.fields.items():
        if fname.startswith("_derived:"):
            continue
        if fname not in df.columns:
            continue
        ftype = meta.get("type", "text")
        col = df[fname]
        non_null = int(col.notna().sum())
        null_rate = round(1 - non_null / n, 4) if n else 0.0
        rec: dict[str, Any] = {
            "type": ftype,
            "non_null": non_null,
            "null_rate": null_rate,
            "cardinality": None,
        }

        if ftype in ("category", "text"):
            vc = col.dropna().astype(str).value_counts()
            rec["cardinality"] = int(vc.shape[0])
            if approx and _SKETCH_AVAIL:
                # 用 HLL sketch 估算去重基数（免去巨型哈希集，适合超大数据）
                h = ds.hll_sketch(14)
                for v in col.dropna().astype(str).to_numpy(dtype="object"):
                    h.update(str(v))
                rec["cardinality_approx"] = int(round(h.get_estimate()))
            rec["top_values"] = [[str(k), int(v)] for k, v in vc.head(10).items()]
        elif ftype == "bool":
            trues = int(col.astype(str).isin(["Y", "是", "True", "true", "1"]).sum())
            rec["true"] = trues
            rec["false"] = int(non_null - trues)
        elif ftype == "numeric":
            s = pd.to_numeric(col, errors="coerce")
            if s.notna().any():
                if approx and _SKETCH_AVAIL:
                    # 用 KLL sketch 估算分位数（免去全量排序，适合超大数据）
                    kll = ds.kll_floats_sketch(200)
                    for v in s.dropna().to_numpy(dtype="float64"):
                        kll.update(float(v))
                    rec.update({
                        "min": _r(s.min()), "max": _r(s.max()),
                        "mean": _r(s.mean()), "std": _r(s.std()),
                        "p1": _r(kll.get_quantile(0.01)),
                        "p25": _r(kll.get_quantile(0.25)),
                        "median": _r(kll.get_quantile(0.50)),
                        "p75": _r(kll.get_quantile(0.75)),
                        "p99": _r(kll.get_quantile(0.99)),
                        "approx_quantiles": True,
                    })
                else:
                    desc = s.describe(percentiles=[.01, .25, .5, .75, .99])
                    rec.update({
                        "min": _r(desc.get("min")), "max": _r(desc.get("max")),
                        "mean": _r(desc.get("mean")), "median": _r(desc.get("50%")),
                        "std": _r(desc.get("std")),
                        "p1": _r(desc.get("1%")), "p25": _r(desc.get("25%")),
                        "p75": _r(desc.get("75%")), "p99": _r(desc.get("99%")),
                    })

        # 跨业态稀疏字段：按业态拆分布局（让解析器能按业态路由）
        if 业态_vals is not None and null_rate > (1 - SPARSE_COVERAGE):
            try:
                cov = col.notna().groupby(业态_vals, dropna=True).mean()
                rec["by_业态"] = {str(k): round(float(v), 4) for k, v in cov.items()}
            except Exception:
                pass

        fields[fname] = rec

    # 整体画像：关键维度分布（供开局定向）
    overall: dict[str, list] = {}
    for dim in ("规划大区", "省份", "城市", "渠道组", "细分渠道", "经营场所"):
        if dim in df.columns:
            vc = df[dim].value_counts(dropna=True)
            overall[dim] = [[str(k), int(v)] for k, v in vc.head(15).items()]

    return {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "data_path": str(data_path) if data_path else None,
        "业态_field": 业态_field,
        "n_rows": n,
        "n_fields": len(fields),
        "overall": overall,
        "fields": fields,
    }


# ----------------------------- 加载 / 缓存 -----------------------------
def load_profile(data_path: Path | None = None, reg: FieldRegistry | None = None,
                 force: bool = False, use_cache: bool = True,
                 approx: bool = False) -> dict | None:
    """加载画像：优先读缓存（mtime 不旧于数据），否则构建并写缓存。"""
    from agent_core import load_store_data, find_default_data
    if data_path is None:
        try:
            data_path = find_default_data()
        except Exception:
            return None
    data_path = Path(data_path)
    reg = reg or FieldRegistry.load()
    if approx:
        # 近似画像按需实时构建，不读不写缓存（与精确画像隔离，避免混淆）
        try:
            df = load_store_data(data_path, reg=reg, use_cache=True)
        except Exception:
            return None
        return build_profile(df, reg, data_path=data_path, approx=True)
    cache = profile_cache_path(data_path)
    if use_cache and not force and cache.exists() and data_path.exists() \
            and cache.stat().st_mtime >= data_path.stat().st_mtime:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        df = load_store_data(data_path, reg=reg, use_cache=True)
    except Exception:
        return None
    prof = build_profile(df, reg, data_path=data_path)
    try:
        cache.write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return prof


def set_profile(prof: dict | None) -> None:
    global PROFILE
    PROFILE = prof


def get_profile() -> dict | None:
    return PROFILE


# ----------------------------- 按业态路由模糊度量词 -----------------------------
def route_metric(term: str, 业态: str | None = None,
                 profile: dict | None = None) -> str | None:
    """把模糊度量词（评分/客流/潜力…）按业态路由到具体物理字段。

    - 有画像：取候选字段中「在该业态覆盖率最高」者；业态未知则用整体覆盖率。
    - 无画像（profile=None）：回退静态默认，保持现有解析行为不回归。
    """
    cands = METRIC_TERM_FIELDS.get(term)
    if not cands:
        return None
    if profile is None:
        profile = PROFILE  # 回退到进程内 set_profile 注入的画像（否则永远走静态默认）
    if not profile:
        return STATIC_DEFAULT_FIELD.get(term)
    fields = profile.get("fields", {})
    best, best_cov = None, -1.0
    for f in cands:
        if f not in fields:
            continue
        cov = 1 - fields[f].get("null_rate", 1)
        if 业态:
            by = fields[f].get("by_业态")
            if by and 业态 in by:
                cov = by[业态]
        if cov > best_cov:
            best_cov, best = cov, f
    return best or STATIC_DEFAULT_FIELD.get(term)


# ----------------------------- 反哺 data_schema.md -----------------------------
def gen_schema_md(profile: dict, reg: FieldRegistry, out_path: Path) -> None:
    """用真实画像数字生成 references/data_schema.md，替换手写过期数字。"""
    overall = profile.get("overall", {})
    fields = profile.get("fields", {})
    n = profile.get("n_rows", 0)

    def tbl(rows, headers):
        head = "| " + " | ".join(headers) + " |"
        sep = "|" + "|".join(["---"] * len(headers)) + "|"
        body = "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows)
        return head + "\n" + sep + "\n" + body

    lines = [
        "# 售点洞察数据字典（参考数据画像 · 由 profiler 自动生成）",
        "",
        f"> 数据来源：`<data>.csv`（约 {n:,} 行 × {profile.get('n_fields', 0)} 字段）。",
        "> 本文件由 `scripts/profiler.py` 的 `gen_schema_md` 依据真实数据自动生成，",
        "> 数字随数据刷新，不会过期。供解析器、LLM 兜底与排查问题时参考。",
        "",
        "## 1. 总体画像",
        "",
    ]

    if "省份" in overall:
        rows = [[k, f"{v:,}"] for k, v in overall["省份"]]
        lines.append("**各省份行数**：")
        lines.append(tbl(rows, ["省份", "行数"]))
        lines.append("")

    if "渠道组" in overall:
        total = sum(v for _, v in overall["渠道组"]) or 1
        rows = [[k, f"{v:,}", f"{v/total*100:.1f}%"] for k, v in overall["渠道组"]]
        lines.append("**渠道组（业态）分布**：")
        lines.append(tbl(rows, ["渠道组", "行数", "占比"]))
        lines.append("")

    # 关键字段覆盖率（从画像取真实覆盖量）
    lines.append("## 2. 关键字段覆盖情况（真实统计）")
    lines.append("")
    note_fields = [
        ("携程-房间数", "住宿筛选"),
        ("携程-星级", "住宿星级"),
        ("携程-综合评分", "住宿评分"),
        ("大众点评餐饮点评评分星级", "餐饮点评评分"),
        ("大众点评餐饮人均消费", "餐饮人均消费"),
        ("周末均值客流(人)", "周末客流"),
        ("平日均值客流(人)", "平日客流"),
        ("月均收入潜力预测(无协议无冰柜)", "潜力排行"),
        ("LINX MT黄金门店", "黄金门店"),
        ("连锁品牌名称", "品牌筛选"),
        ("经营场所名称", "餐饮街/商圈聚合"),
    ]
    rows = []
    for f, use in note_fields:
        if f in fields:
            cov = fields[f]
            non_null = cov.get("non_null", 0)
            nr = f"{non_null:,}" if non_null else "—"
            extra = ""
            if cov.get("type") == "numeric" and cov.get("median") is not None:
                extra = f"（median {cov['median']}）"
            elif cov.get("top_values"):
                top = cov["top_values"][0]
                extra = f"（Top: {top[0]} {top[1]:,}）"
            rows.append([f, nr, use + extra])
    lines.append(tbl(rows, ["字段", "覆盖量", "用途"]))
    lines.append("")

    lines += [
        "## 3. 三类查询对应的字段入口",
        "",
        "- **A 明确条件**：`规划大区` `省份` `城市` `区县` `渠道组` `子渠道` `细分渠道` "
        "`连锁品牌名称` `是否连锁` `携程-房间数` `携程-星级` `LINX MT黄金门店` `经营场所` `经营场所名称`",
        "- **B 分布探索**：`group_by` 常用 `渠道组` / `子渠道` / `细分渠道` / `经营场所` / "
        "`经营场所名称` / `规划大区` / `城市`；`metrics` 用 `count`（数值字段可 `avg`/`sum`）",
        "- **C 规则排行**：候选维度默认 `经营场所名称`；规则常用 `count`、`_derived:开店年限`、"
        "`大众点评餐饮点评评分星级`、`周末均值客流(人)`",
        "",
        "## 4. 字段语义映射",
        "",
        "口语别名 → 真实字段的映射表见 `references/field_registry.json`（解析器加载它）。",
        "新增可识别的口语词，直接在该 JSON 的 `alias` / `synonyms` 里加即可，无需改代码。",
        "",
        "## 5. 统计画像层（profiler）",
        "",
        "- 字段真实统计由 `scripts/profiler.py` 计算并缓存到数据同级的 `<数据>.profile.json`。",
        "- 模糊度量词（评分/客流/潜力/房间数…）由 `route_metric` 按业态覆盖率路由到正确字段。",
        "- 查看：`agent_cli.py --profile [字段]`；外部 Agent 经 MCP `profile` tool 按需查询。",
        "",
    ]
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
