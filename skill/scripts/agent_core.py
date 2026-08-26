"""
agent_core.py
=============
通用查询智能体 — 执行引擎（不依赖 LLM）。

三类执行器：
  A. FilterQuery      明确条件过滤（+ 排序 + 限制行数）
  B. DistributionQry  过滤 + 维度聚合 + 排行
  C. RuleRankQuery    候选聚合 + 多规则评分 + 高/中/低评级

输入：Filter / DSL JSON
输出：pandas DataFrame + 解释 trace
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from bizhours import close_after as _biz_close_after, open_after as _biz_open_after

REG_PATH = Path(__file__).parent.parent / "references" / "field_registry.json"

# 语义时间算子：解析「大众点评营业时间」自由文本，由执行器解释，不按字段类型校验
SEMANTIC_TIME_OPS = {"close_after", "open_after"}


# ----------------------------- 字段注册表 -----------------------------
# 各字段类型允许的算子（确定性编译护栏用）
_OP_BY_TYPE = {
    "category": {"=", "!=", "in", "not_in", "contains", "not_contains",
                 "contains_any", "contains_all", "is_null", "not_null"},
    "text": {"=", "!=", "contains", "not_contains", "contains_any",
             "contains_all", "is_null", "not_null"},
    "bool": {"=", "!=", "is_null", "not_null"},
    "numeric": {"=", "!=", ">", ">=", "<", "<=", "between", "in",
                "is_null", "not_null"},
    "id": {"=", "!=", "contains", "in", "is_null", "not_null"},
    "date": {"=", "!=", ">", ">=", "<", "<=", "between", "is_null", "not_null"},
    "datetime": {"=", "!=", ">", ">=", "<", "<=", "between", "is_null", "not_null"},
    "derived_numeric": {"=", "!=", ">", ">=", "<", "<=", "between",
                        "is_null", "not_null"},
    "json_text": {"contains", "not_contains", "is_null", "not_null"},
}


@dataclass
class FieldRegistry:
    fields: dict
    synonyms: dict  # alias / synonyms 平铺
    measures: dict = dc_field(default_factory=dict)     # 业务度量（显式口径）
    entities: dict = dc_field(default_factory=dict)     # 私有实体字典

    @classmethod
    def load(cls, path: Path = REG_PATH) -> "FieldRegistry":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        syn: dict[str, str] = {}
        for fname, meta in d["fields"].items():
            for a in meta.get("alias", []) or []:
                syn[a] = fname
                syn[fname] = fname
            for k, v in (meta.get("synonyms") or {}).items():
                # k: 口语词 -> 真实字段名 fname
                syn[k] = fname
                # v: 可能是 str（直接是字段名）或 list（一组口语同义词）
                if isinstance(v, list):
                    for vi in v:
                        syn[vi] = fname
                else:
                    syn[v] = fname
        return cls(
            fields=d["fields"],
            synonyms=syn,
            measures=d.get("measures", {}),
            entities=d.get("domain_entities", {}),
        )

    # —— 度量 / 实体解析（语义层中介）——
    def resolve_measure(self, name: str) -> dict | None:
        """返回度量的底层字段信息；bool 度量带 op/value，numeric 度量只有 field。"""
        return self.measures.get(name)

    def resolve_entity(self, name: str) -> dict | None:
        """返回私有实体的确定性过滤规则 {field, op, value}。"""
        return self.entities.get(name)

    def validate_against(self, df: pd.DataFrame) -> dict:
        """离线探索期自检（对应 Data-Copilot 的 exploration phase）：
        校验语义层注册字段是否真实存在于数据，并给出关键度量的覆盖率，
        让运行期前就能知道'哪些业务语义可用、覆盖如何'。
        """
        cols = set(df.columns)
        missing_fields = [f for f in self.fields if f not in cols
                          and not f.startswith("_derived:")]
        derived_missing = [f for f in self.fields if f.startswith("_derived:")
                           and f not in cols]
        measure_cov: dict[str, float] = {}
        for mname, m in self.measures.items():
            f = m["field"]
            if f in cols:
                cov = df[f].notna().mean() * 100
                measure_cov[mname] = round(cov, 1)
        return {
            "registered_fields": len(self.fields),
            "present_fields": len(self.fields) - len(missing_fields),
            "missing_fields": missing_fields,
            "derived_missing": derived_missing,
            "measure_coverage": measure_cov,
            "entities": list(self.entities.keys()),
        }

    def resolve(self, name_or_alias: str) -> str:
        if name_or_alias in self.fields:
            return name_or_alias
        if name_or_alias in self.synonyms:
            return self.synonyms[name_or_alias]
        # 大小写 / 空白兜底
        n = name_or_alias.strip()
        if n in self.fields:
            return n
        if n in self.synonyms:
            return self.synonyms[n]
    # ------------------------------------------------------------------
    # v1.2.0 升级: W3C PROV-O 实体溯源注记 (Provenance Annotation)
    # 每个 domain_entity 都带 uri 与 provenance, 在 AnalysisIntent 中展示
    # 业务人员可查验 "我的理解来自哪里"。
    # ------------------------------------------------------------------
    def entity_provenance(self, name: str) -> dict | None:
        """返回 domain_entity 的溯源信息 (uri, provenance, 来源标准/权威)。"""
        ent = self.entities.get(name)
        if not ent:
            return None
        prov = ent.get("provenance") or {}
        return {
            "entity_name": name,
            "uri": ent.get("uri"),
            "logical_rule": {
                "field": ent.get("field"),
                "op": ent.get("op"),
                "value": ent.get("value"),
                "composite_rule": ent.get("composite_rule"),
            },
            "provenance": prov,
            "tier": prov.get("confidence_tier", "UNVERIFIED"),
            "source_label": prov.get("authority") or prov.get("source") or "TopPrism KB",
        }

    def diagnose_text(self, query: str) -> list[dict]:
        """对业务口语问句做反向归因, 找出触发的所有受管实体及其溯源。

        优先调用 fmcg_knowledge_engine (W3C PROV-O 强校验), 失败时回退到本地语义层。
        返回 [{type, uri, matched_label, mapped_rule, provenance}, ...]
        """
        out: list[dict] = []
        q = query or ""
        # 本地回退: 扫所有 domain_entities + measure + brand alias
        for ename, ent in self.entities.items():
            labels = [ename] + ([ent.get("uri")] if ent.get("uri") else [])
            if any(lab and (lab in q or ent.get("value") in q if isinstance(ent.get("value"), str) else False) for lab in labels):
                prov = ent.get("provenance") or {}
                out.append({
                    "type": "DomainEntity",
                    "uri": ent.get("uri"),
                    "matched_label": ename,
                    "mapped_rule": ent,
                    "provenance": prov,
                    "confidence_tier": prov.get("confidence_tier", "UNVERIFIED"),
                })
        # 增强: 调用 fmcg_knowledge_engine 做 PROV-O 级诊断
        try:
            from fmcg_knowledge_engine import FMCGKnowledgeBase  # type: ignore
            kb = FMCGKnowledgeBase()
            extra = kb.diagnose_text(q)
            for e in extra:
                # 去重: 同 uri 仅保留 PROV-O 强校验的版本
                if not any(o.get("uri") == e.get("uri") for o in out):
                    out.append(e)
        except Exception:
            pass
        return out


# ----------------------------- 工具：单条件判断 -----------------------------
def match_one(series: pd.Series, op: str, value: Any) -> pd.Series:
    """单个 (field, op, value) 条件 → 布尔 Series。"""
    if op in ("=", "==", "eq"):
        return series == value
    if op in ("!=", "<>", "ne"):
        return series != value
    if op in (">", "gt"):
        return pd.to_numeric(series, errors="coerce") > value
    if op in (">=", "ge"):
        return pd.to_numeric(series, errors="coerce") >= value
    if op in ("<", "lt"):
        return pd.to_numeric(series, errors="coerce") < value
    if op in ("<=", "le"):
        return pd.to_numeric(series, errors="coerce") <= value
    if op == "in":
        return series.isin(value if isinstance(value, (list, tuple, set)) else [value])
    if op == "not_in":
        return ~series.isin(value if isinstance(value, (list, tuple, set)) else [value])
    if op == "contains":
        return series.astype(str).str.contains(str(value), na=False, regex=False)
    if op == "not_contains":
        return ~series.astype(str).str.contains(str(value), na=False, regex=False)
    if op == "contains_any":
        # value 是 list，命中任一
        pat = "|".join(re.escape(str(v)) for v in value)
        return series.astype(str).str.contains(pat, na=False, regex=True)
    if op == "contains_all":
        # value 是 list，全部命中
        out = pd.Series(True, index=series.index)
        for v in value:
            out &= series.astype(str).str.contains(re.escape(str(v)), na=False, regex=False)
        return out
    if op == "is_null":
        return series.isna()
    if op == "not_null":
        return series.notna()
    if op == "between":
        lo, hi = value
        s = pd.to_numeric(series, errors="coerce")
        return (s >= lo) & (s <= hi)
    if op == "close_after":
        # 营业到 value 点(24h)以后才打烊；解析「大众点评营业时间」自由文本
        ref = float(value)
        return series.apply(lambda v: _biz_close_after(v, ref))
    if op == "open_after":
        # value 点(24h)以后才开门（夜宵店句式）
        ref = float(value)
        return series.apply(lambda v: _biz_open_after(v, ref))
    raise ValueError(f"unknown op: {op}")


# ----------------------------- 派生字段 -----------------------------
def add_derived(df: pd.DataFrame, reg: FieldRegistry) -> pd.DataFrame:
    """添加 _derived:* 字段。"""
    if "_derived:开店年限" in reg.fields and "_derived:开店年限" not in df.columns:
        ts = pd.to_datetime(df["POI上线时间"], errors="coerce")
        now = pd.Timestamp.now()
        df["_derived:开店年限"] = ((now - ts).dt.days / 365.25).round(2)
    return df


# ----------------------------- 数据加载（含 parquet 缓存） -----------------------------
def find_default_data() -> Path:
    """自动发现售点洞察 CSV。

    优先级：环境变量 OUTLET_DATA → ~/outlet-insight/data → ~/Downloads
    下文件名包含「售点洞察/售点」的 CSV。
    找不到时抛出 FileNotFoundError，调用方应改用 --data 显式指定。
    """
    import os

    env = os.environ.get("OUTLET_DATA")
    if env:
        return Path(env)
    # 多项目根兼容: 本仓库 / ~/outlet-insight/data / ~/Downloads
    script_dir = Path(__file__).resolve().parent.parent
    candidates = [script_dir / "data", Path.home() / "outlet-insight" / "data", Path.home() / "Downloads"]
    for base in candidates:
        if not base.exists():
            continue
        # 多模式匹配,兼容本地原始命名 (SH全量售点洞察*.csv) 与脱敏占位符 (<data>.csv)
        for pat in ("<data>.csv", "*售点洞察*.csv", "*售点*.csv", "*.csv"):
            hits = sorted(base.glob(pat))
            if hits:
                return hits[-1]
    raise FileNotFoundError(
        "未在项目 data/ 或 ~/outlet-insight/data 或 ~/Downloads 找到售点数据，"
        "请通过 --data 指定 CSV 路径，或设置环境变量 OUTLET_DATA"
    )


def load_store_data(path: Path | str | None = None, reg: FieldRegistry | None = None,
                    use_cache: bool = True) -> pd.DataFrame:
    """加载售点数据，自动用 parquet 缓存加速（<size> MB CSV 首次约 15-25s，之后 <3s）。

    参数：
      path        CSV 或 parquet 路径；为 None 时走 find_default_data()
      reg         FieldRegistry；None 时自动加载
      use_cache   True 时对 CSV 生成/复用同名 .parquet 缓存
    """
    path = Path(path) if path else find_default_data()
    if reg is None:
        reg = FieldRegistry.load()

    pq = path.with_suffix(".parquet")
    if use_cache and pq.exists() and pq.stat().st_mtime >= path.stat().st_mtime:
        df = pd.read_parquet(pq)
    elif path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, low_memory=False)
        if use_cache:
            try:
                df.to_parquet(pq, index=False)
            except Exception as e:  # 缓存失败不致命
                print(f"[warn] 写 parquet 缓存失败: {e}")

    df = add_derived(df, reg)
    return df


# ----------------------------- 工具：执行一组 filter -----------------------------
def apply_filters(df: pd.DataFrame, filters: list[dict], reg: FieldRegistry) -> tuple[pd.DataFrame, list[str]]:
    """应用 filter 列表，返回 (过滤后df, trace)。"""
    trace: list[str] = []
    if not filters:
        return df, trace
    mask = pd.Series(True, index=df.index)
    for f in filters:
        field_name = reg.resolve(f["field"])
        op = f["op"]
        value = f.get("value")
        if field_name not in df.columns:
            trace.append(f"[skip] 字段 {field_name!r} 不存在")
            continue
        m = match_one(df[field_name], op, value)
        n = int(m.sum())
        trace.append(f"[filter] {field_name} {op} {value!r}  → {n:,} 行")
        mask &= m
    return df[mask], trace


# ============================== A. 明确查询 ==============================
@dataclass
class FilterQuery:
    filters: list[dict] = dc_field(default_factory=list)
    order_by: list[dict] = dc_field(default_factory=list)   # [{field, dir}]
    limit: int | None = None
    select: list[str] | None = None   # 指定返回列
    group_by: list[str] | None = None # 如果 group_by 给出，则做 B 类聚合
    metrics: list[dict] | None = None # [{name, field, agg}]

    def run(self, df: pd.DataFrame, reg: FieldRegistry) -> tuple[pd.DataFrame, list[str]]:
        trace: list[str] = []
        df, t = apply_filters(df, self.filters, reg); trace += t
        trace.append(f"[A] 过滤后行数: {len(df):,}")

        # group_by + 聚合（B 类分布）
        if self.group_by:
            gb_cols = [reg.resolve(c) for c in self.group_by]
            metrics = self.metrics or [{"name": "count", "agg": "count"}]
            # 选取一个能作为 groupby 键的列；count 用任一列都行
            any_col = reg.resolve(metrics[0].get("field")) if metrics[0].get("field") else None
            if any_col is None or any_col not in df.columns:
                any_col = gb_cols[0]
            agg_dict: dict[str, Any] = {}
            for m in metrics:
                agg = m["agg"]
                alias = m.get("name") or f"{m.get('field','_')}_{agg}"
                if agg == "count":
                    # 用 size() 更稳
                    out = df.groupby(gb_cols, dropna=False).size().reset_index(name=alias)
                    out = out.sort_values(alias, ascending=False)
                    if self.limit:
                        out = out.head(self.limit)
                    trace.append(f"[B] group_by {gb_cols} → {len(out):,} 个分组 (count)")
                    return out, trace
                fname = reg.resolve(m["field"])
                agg_dict[alias] = pd.NamedAgg(column=fname, aggfunc=agg)
            out = df.groupby(gb_cols, dropna=False).agg(**agg_dict).reset_index()
            # 默认按主指标降序
            primary = metrics[0].get("name") or f"{metrics[0]['field']}_{metrics[0]['agg']}"
            out = out.sort_values(primary, ascending=False)
            if self.limit:
                out = out.head(self.limit)
            trace.append(f"[B] group_by {gb_cols} → {len(out):,} 个分组")
            return out, trace

        # 记录全量池规模
        pool_size = len(df)

        # 排序
        if self.order_by:
            for o in self.order_by:
                col = reg.resolve(o["field"])
                if col in df.columns:
                    asc = (o.get("dir", "desc").lower() == "asc")
                    df = df.sort_values(col, ascending=asc, na_position="last")
                    trace.append(f"[order] {col} {'asc' if asc else 'desc'}")
        # 限制行数 / 配额截断
        if self.limit:
            selected_df = df.head(self.limit).copy()
            trace.append(f"[limit] 目标配额前 {self.limit} 行 (全量候选池: {pool_size:,})")
        else:
            selected_df = df
        # 列裁剪
        if self.select:
            sel = [reg.resolve(c) for c in self.select if reg.resolve(c) in selected_df.columns]
            selected_df = selected_df[sel]
        selected_df.attrs["pool_size"] = pool_size
        return selected_df, trace

# ============================== C. 规则排行 ==============================
@dataclass
class Rule:
    name: str
    kind: str   # "count" | "ratio" | "sum" | "avg" | "min" | "max" | "order"
    field: str | None = None
    op: str | None = None
    value: Any = None
    filters: list[dict] = dc_field(default_factory=list)   # 在候选内进一步过滤
    direction: str = "desc"  # for kind=order
    weight: float = 1.0
    # 兼容解析层冗余字段（如 value_ratio/op_ratio）
    value_ratio: Any = None
    op_ratio: Any = None
    score: Any = None
    notes: Any = None

    def effective_value(self) -> Any:
        """ratio 类规则：优先用 value_ratio（更精确）。"""
        if self.kind == "ratio" and self.value_ratio is not None:
            return self.value_ratio
        return self.value


@dataclass
class RuleRankQuery:
    candidate_field: str               # 用什么字段做候选分组
    candidate_filter: list[dict] = dc_field(default_factory=list)
    min_candidate_count: int = 1
    rules: list[Rule] = dc_field(default_factory=list)
    grade_high: float = 0.8            # 满足率 ≥ high = 高
    grade_mid: float = 0.3             # 满足率 ≥ mid = 中；否则 低
    top_n: int | None = None           # 输出前 N
    # 哪些 rule 的"达标"参与综合得分；默认全部
    score_rules: list[str] | None = None

    def run(self, df: pd.DataFrame, reg: FieldRegistry) -> tuple[pd.DataFrame, list[str]]:
        trace: list[str] = []
        cand_field = reg.resolve(self.candidate_field)
        trace.append(f"[C] 候选字段: {cand_field}")

        # 0) 排除候选为空的
        base = df[df[cand_field].notna()].copy()
        trace.append(f"[C] 候选非空行数: {len(base):,}")

        # 1) 候选层过滤
        base, t = apply_filters(base, self.candidate_filter, reg)
        trace += [f"[C.cand] " + x for x in t]
        trace.append(f"[C] 候选过滤后行数: {len(base):,}")

        # 候选基数过滤
        cand_counts = base.groupby(cand_field).size().rename("_candidate_count")
        valid_cands = cand_counts[cand_counts >= self.min_candidate_count].index
        base = base[base[cand_field].isin(valid_cands)]
        trace.append(f"[C] 候选数(基数≥{self.min_candidate_count}): {len(valid_cands):,}")

        # 2) 计算每条 rule
        rule_summary: dict[str, pd.Series] = {}
        for r in self.rules:
            sub, t = apply_filters(base, r.filters, reg)
            trace += [f"[C.rule:{r.name}] " + x for x in t]
            g = sub.groupby(cand_field)

            if r.kind == "count":
                v = g.size().rename(r.name)
            elif r.kind == "sum":
                v = g[r.field].apply(pd.to_numeric, errors="coerce").sum(min_count=1).rename(r.name)
            elif r.kind == "avg":
                v = g[r.field].apply(pd.to_numeric, errors="coerce").mean().rename(r.name)
            elif r.kind in ("min", "max"):
                v = getattr(g[r.field].apply(pd.to_numeric, errors="coerce"), r.kind)().rename(r.name)
            elif r.kind == "ratio":
                num_mask = match_one(sub[r.field], r.op, r.value)
                num = num_mask.groupby(sub[cand_field]).sum()
                den = sub.groupby(cand_field).size()
                v = (num / den).fillna(0).rename(r.name)
            elif r.kind == "order":
                # 用于"按某字段排序"，不参与达标率；用 sum 作为该候选的代表值
                s = pd.to_numeric(sub[r.field], errors="coerce")
                v = s.groupby(sub[cand_field]).sum().rename(r.name)
            else:
                trace.append(f"[C.rule:{r.name}] 未知 kind={r.kind}, 跳过")
                continue
            rule_summary[r.name] = v
            trace.append(f"[C.rule:{r.name}] 计算完成 (kind={r.kind}, field={r.field})")

        # 3) 合并
        out = pd.concat(rule_summary.values(), axis=1).reset_index()
        out = out.merge(cand_counts.rename("_candidate_count").reset_index(), on=cand_field, how="left")

        # 4) 规则达标判断 + 综合得分
        score_rule_names = self.score_rules or [r.name for r in self.rules if r.kind in ("count", "ratio")]
        sat = pd.DataFrame(index=out.index)
        for r in self.rules:
            if r.name not in out.columns:
                continue
            if r.kind == "count":
                sat[r.name] = (out[r.name] >= r.value).astype(int)
            elif r.kind == "ratio":
                sat[r.name] = (out[r.name] >= r.effective_value()).astype(int)
            elif r.kind == "sum":
                sat[r.name] = (out[r.name] >= r.value).astype(int) if r.value is not None else 0
            else:
                # order / avg / min / max：作为"参考列"，不参与达标率
                sat[r.name] = 0
        out["达标率"] = sat[score_rule_names].mean(axis=1) if score_rule_names else 0
        out["等级"] = pd.cut(
            out["达标率"],
            bins=[-0.01, self.grade_mid, self.grade_high, 1.01],
            labels=["低", "中", "高"],
        )

        # 5) 排序：综合得分 desc，再按主 rule desc
        order_cols = ["达标率"]
        asc = [False]
        for r in self.rules:
            if r.kind in ("order", "sum", "avg", "min", "max") and r.name in out.columns:
                order_cols.append(r.name)
                asc.append(r.direction != "desc")
        out = out.sort_values(order_cols, ascending=asc, na_position="last").reset_index(drop=True)
        if self.top_n:
            out = out.head(self.top_n)
        trace.append(f"[C] 输出 {len(out):,} 行（top_n={self.top_n}）")
        return out, trace


# ----------------------------- 数据质量与适用性评估 -----------------------------
# 用于分析置信度与适用边界评估的关键维度。name=真实字段，label=展示名。
AUDIT_DIMENSIONS = [
    ("大众点评餐饮点评评分星级", "点评评分"),
    ("周末均值客流(人)", "周末客流"),
    ("平日均值客流(人)", "平日客流"),
    ("_derived:开店年限", "开店年限"),
    ("携程-房间数", "房间数"),
    ("携程-星级", "携程星级"),
]
AUDIT_COMPLETE_THRESHOLD = 0.5   # 候选关键字段非空率低于 50% → 数据不足
AUDIT_DIM_MIN_COVERAGE = 0.50   # 维度在候选群覆盖率低于 50% → 视为不适用，剔除（按主业态自动选维度）

def _audit_dims(reg: FieldRegistry):
    return [(reg.resolve(f), lbl) for f, lbl in AUDIT_DIMENSIONS]

def audit(df: pd.DataFrame, query: dict, out: pd.DataFrame,
          reg: FieldRegistry, kind: str):
    """对查询结果做数据质量体检，返回 (out_可能加列, report_dict)。

    - C 类：按候选回表，给每个候选算关键字段完整度，out 增加「数据完整度%」列
    - A 类：按结果行回表，给出关键字段整体缺失率
    - B 类：分布结果无逐行数据，跳过逐行体检

    关键设计：维度若在某候选群里覆盖率 < 5%（如纯餐饮候选的"房间数"），
    视为该群体不适用，自动从审计中剔除，避免误报"数据缺失"。
    report 含 field_missing / incomplete_count / flags（具体问题）/ recommendation（数据质量与适用性评估建议）。
    """
    dims = _audit_dims(reg)
    report = {"audited": True,
               "scope": "candidates" if kind == "C" else "resultset",
               "n": len(out)}
    flags = []

    if kind == "C":
        cand_field = reg.resolve(query["candidate_field"])
        cands = out[cand_field].tolist()
        sub = df[df[cand_field].isin(cands)]
        recs = []
        for cand, g in sub.groupby(cand_field):
            rec = {cand_field: cand}
            for fname, lbl in dims:
                col = lbl + "_完整度"
                # 仅当该候选自身有此字段数据才计入（纯餐饮候选的房间数视为不适用）
                if fname in g.columns and g[fname].notna().any():
                    rec[col] = round(g[fname].notna().mean() * 100, 1)
                else:
                    rec[col] = None
            recs.append(rec)
        comp = pd.DataFrame(recs)
        all_score_cols = [lbl + "_完整度" for fname, lbl in dims]
        # 候选数据完整度 = 其适用维度的平均（skipna 跳过不适用维度）
        comp["数据完整度%"] = comp[all_score_cols].mean(axis=1, skipna=True).round(1)
        # 群体层面只报告"在足够多候选里适用"的维度（阈值 20%），避免误报
        valid_lbls = [
            lbl for fname, lbl in dims
            if comp[lbl + "_完整度"].notna().mean() >= AUDIT_DIM_MIN_COVERAGE
        ]
        out = out.merge(comp[[cand_field, "数据完整度%"]], on=cand_field, how="left")
        thr = AUDIT_COMPLETE_THRESHOLD * 100
        n_low = int((comp["数据完整度%"] < thr).sum())
        field_missing = {
            lbl: round(100 - comp[lbl + "_完整度"].mean(skipna=True), 1)
            for lbl in valid_lbls
        }
        report.update({
            "field_missing": field_missing,
            "threshold": AUDIT_COMPLETE_THRESHOLD,
            "incomplete_count": n_low,
            "incomplete_ratio": round(n_low / len(comp) * 100, 1) if len(comp) else 0,
            "audited_dimensions": valid_lbls,
        })
        for lbl in valid_lbls:
            # 只对"适用该维度"的候选计数（不适用者不算缺失）
            nb = int(((comp[lbl + "_完整度"].notna()) & (comp[lbl + "_完整度"] < thr)).sum())
            if nb > 0:
                flags.append(f"{nb} 个候选「{lbl}」完整度低于 {int(thr)}%，建议补充采集以提高分析置信度")
    elif kind == "A":
        base = df.loc[out.index.intersection(df.index)]
        field_missing = {}
        if len(base):
            sample_base = base.sample(n=min(5000, len(base)), random_state=42) if len(base) > 5000 else base
            for fname, lbl in dims:
                if fname not in sample_base.columns:
                    continue
                cov = float(sample_base[fname].notna().mean())
                if cov < 0.05:   # 该业态基本无此字段 → 不适用，跳过
                    continue
                miss = round((1 - cov) * 100, 1)
                field_missing[lbl] = miss
                if miss > 50:
                    flags.append(f"结果集中「{lbl}」缺失率 {miss}%，作为关键分析依据前建议补充采集")
        report["field_missing"] = field_missing
        report["note"] = "分布类结果无逐行数据，未做逐行体检"

    if kind == "C":
        nl = report.get("incomplete_count", 0)
        if nl == 0:
            rec = "全部候选数据完整度达标，可作为有效分析依据"
        elif report["incomplete_ratio"] < 30:
            rec = f"绝大多数候选数据可用，{nl} 个数据偏弱，分析时需注意置信度"
        else:
            rec = f"{nl} 个候选（{report['incomplete_ratio']}%）数据完整度偏低，建议补充采集后再行评估"
    else:
        high = [lbl for lbl, v in report.get("field_missing", {}).items() if v > 50]
        rec = (f"结果集中 {', '.join(high)} 缺失率偏高，作为分析依据前建议补充采集"
               if high else "关键字段完整度良好，可作为分析参考")
    report["recommendation"] = rec
    report["flags"] = flags

    # 目标配额达成与截断评估 (Quota Evaluation)
    target_quota = query.get("target_quota") or query.get("limit")
    if target_quota and kind == "A":
        pool_size = out.attrs.get("pool_size", len(out))
        selected_size = len(out)
        fulfillment_rate = round(selected_size / target_quota, 2) if target_quota else 1.0
        fulfillment_status = "fulfilled" if selected_size >= target_quota else "gap_detected"
        gap_count = max(0, target_quota - selected_size)

        cutoff_thresholds = {}
        if len(out) > 0:
            last_row = out.iloc[-1]
            for num_col in ["周末均值客流(人)", "大众点评餐饮点评评分星级", "月均收入潜力预测(无协议无冰柜)", "携程-房间数", "营业面积"]:
                if num_col in out.columns and pd.notna(last_row[num_col]):
                    val = last_row[num_col]
                    try:
                        cutoff_thresholds[num_col] = round(float(val), 2)
                    except (ValueError, TypeError):
                        cutoff_thresholds[num_col] = str(val)
        report["quota_evaluation"] = {
            "target_quota": int(target_quota),
            "pool_size": int(pool_size),
            "selected_size": int(selected_size),
            "fulfillment_rate": float(fulfillment_rate),
            "fulfillment_status": fulfillment_status,
            "gap_count": int(gap_count),
            "cutoff_thresholds": cutoff_thresholds
        }
    return out, report


# ----------------------------- 确定性编译护栏 -----------------------------
def _expand_semantics(filters: list[dict], reg: FieldRegistry) -> list[dict]:
    """把度量/实体引用展开成物理字段过滤（语义层 → 物理层 的确定性编译）。

    例如 过滤 {field:'餐饮高评分'} → {field:'大众点评餐饮点评评分星级', op:'>=', value:3.8}
    度量名/实体名在语义层不存在时原样返回（交给 validate_query 拦截）。
    """
    out: list[dict] = []
    for f in filters:
        fld = f.get("field")
        if fld in reg.measures:
            m = reg.measures[fld]
            if "op" in m:  # bool 度量：直接展开为 field+op+value
                out.append({"field": m["field"], "op": m["op"], "value": m["value"]})
            else:          # numeric 度量：仅替换物理字段名
                out.append({**f, "field": m["field"]})
        elif fld in reg.entities:
            e = reg.entities[fld]
            out.append({"field": e["field"], "op": e.get("op", "="), "value": e["value"]})
        else:
            out.append(f)
    return out


def validate_query(query: dict, reg: FieldRegistry) -> dict:
    """确定性编译期校验（对应论文 SMQ → Deterministic Compiler 的编译期拦截）。

    返回 {ok, errors, warnings}：
    - 拒绝语义层外的字段（防模型幻觉出不存在的列）
    - 拒绝算子与字段类型不兼容（防 '渠道组 > 5' 这类静默空结果）
    - 拒绝 order_by/group_by/度量引用不存在的字段
    任何 error 都会让 run_query 在编译期抛错，而非运行期静默产出错误数据。
    """
    errors: list[str] = []
    warnings: list[str] = []

    refs: list[tuple[str, str | None, str | None]] = []  # (ctx, field, op)
    for f in query.get("filters", []) + query.get("candidate_filter", []):
        refs.append(("filter", f.get("field"), f.get("op")))
    for o in query.get("order_by", []):
        refs.append(("order_by", o.get("field"), None))
    for g in query.get("group_by", []):
        refs.append(("group_by", g, None))
    for m in query.get("metrics", []) or []:
        if m.get("field"):
            refs.append(("metric", m.get("field"), None))
    if query.get("candidate_field"):
        refs.append(("candidate_field", query["candidate_field"], None))
    for r in query.get("rules", []):
        if r.get("field"):
            refs.append(("rule", r.get("field"), r.get("op")))

    for ctx, name, op in refs:
        if name is None:
            continue
        resolved = reg.resolve(name)
        # 度量/实体名在展开后已是物理字段；这里再兜底允许其原名
        if resolved in reg.fields and reg.fields[resolved].get("isolated"):
            errors.append(f"[{ctx}] 字段「{name}」处于歧义隔离状态（excluded_from_execution），编译期拒绝执行")
            continue
        if resolved not in reg.fields and name not in reg.measures and name not in reg.entities:
            errors.append(f"[{ctx}] 字段「{name}」不在语义层，无法确定其物理含义（疑似幻觉字段，编译期拒绝）")
            continue
        if op is not None and resolved in reg.fields:
            if op in SEMANTIC_TIME_OPS:
                # 时间谓词由执行器解析营业时间文本，绕过按字段类型的算子校验
                continue
            ftype = reg.fields[resolved].get("type", "text")
            allowed = _OP_BY_TYPE.get(ftype, _OP_BY_TYPE["text"])
            if op not in allowed:
                errors.append(
                    f"[{ctx}] 字段「{name}」(类型 {ftype}) 不支持算子 {op}；"
                    f"允许算子: {sorted(allowed)}"
                )
    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


def run_query(df: pd.DataFrame, query: dict, reg: FieldRegistry,
             text: str | None = None,
             ) -> tuple[pd.DataFrame, list[str], str, dict]:
    """统一入口：query = {type: 'A'|'B'|'C', ...}

    执行前先做两道语义层处理：
      1) _expand_semantics —— 度量/实体引用展开为物理字段过滤
      2) validate_query    —— 确定性编译护栏，非法查询直接抛错（不静默出错）
    """
    # 1) 语义展开
    if "filters" in query:
        query["filters"] = _expand_semantics(query["filters"], reg)
    if "candidate_filter" in query:
        query["candidate_filter"] = _expand_semantics(query["candidate_filter"], reg)
    # 2) 编译期校验
    v = validate_query(query, reg)
    if not v["ok"]:
        raise ValueError(
            "查询未通过语义层编译校验（已在编译期拦截，避免产出错误数据）：\n"
            + "\n".join("  • " + e for e in v["errors"])
        )

    qtype = query.get("type", "A")
    if qtype in ("A", "B"):
        q = FilterQuery(
            filters=query.get("filters", []),
            order_by=query.get("order_by", []),
            limit=query.get("limit"),
            select=query.get("select"),
            group_by=query.get("group_by"),
            metrics=query.get("metrics"),
        )
        out, trace = q.run(df, reg)
        is_dist = bool(query.get("group_by"))
        out, ar = audit(df, query, out, reg, "B" if is_dist else "A")
        return out, trace, "A/B", ar
    if qtype == "C":
        rules = [Rule(**r) for r in query.get("rules", [])]
        q = RuleRankQuery(
            candidate_field=query["candidate_field"],
            candidate_filter=query.get("candidate_filter", []),
            min_candidate_count=query.get("min_candidate_count", 1),
            rules=rules,
            grade_high=query.get("grade_high", 0.8),
            grade_mid=query.get("grade_mid", 0.3),
            top_n=query.get("top_n"),
            score_rules=query.get("score_rules"),
        )
        out, trace = q.run(df, reg)
        out, ar = audit(df, query, out, reg, "C")
        return out, trace, "C", ar
    # 3) Provenance Annotation (v1.2.0): 反向归因用户口语问句到受管 FMCG 实体, 挂到 audit 上供 insight 拼装
    if text:
        try:
            annotations = reg.diagnose_text(text)
            if annotations:
                ar.setdefault("interpretation", {})["entities"] = annotations
                ar["provenance_annotations"] = [
                    {"uri": a.get("uri"), "matched_label": a.get("matched_label"),
                     "confidence_tier": a.get("confidence_tier") or a.get("provenance", {}).get("confidence_tier"),
                     "source": (a.get("provenance") or {}).get("source") or (a.get("provenance") or {}).get("authority")}
                    for a in annotations
                ]
        except Exception as exc:  # 不可阻塞执行
            ar["provenance_annotation_error"] = str(exc)
    return out, trace, qtype, ar
