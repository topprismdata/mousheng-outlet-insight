"""
session.py
==========
会话态查询智能体（借鉴 TaskWeaver 状态化 DataFrame 传递）
+ Dry-plan 透明执行计划（借鉴 WrenAI，执行前先展示将要做什么）。

三大能力：
  1) 多步分析：加载一次全量数据后，结果 DataFrame 在步骤间保持（不重复读盘），
     支持「先筛美宜佳 → 再看其分布 → 再进一步筛评分」的连续追问。
  2) 工作集语义：A 类明细结果会成为下一步的「门店级工作集」；
     B/C 聚合结果只展示，不改变工作集（因为聚合行无法映射回门店）。
  3) Dry-plan：执行前先展示 scope / 编译校验 / 筛选 / 聚合 / 排序 / 体检，可审查。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from agent_core import FieldRegistry, load_store_data, find_default_data
from dm_layer import ensure_dm
from engine import run_query as _eng_run_query
from nl_parser import auto_parse, explain_query


# scope 判定关键词：命中则「在上一步结果(当前工作集)上」操作
_STAY_WORDS = ["这些", "其中", "上面", "刚才", "进一步", "再筛", "再查", "结果里",
               "在上一步", "这堆", "这批", "它们", "里面", "之中", "从这里", "这上面"]
# 回到全量关键词
_RESET_WORDS = ["全部", "整个", "重新", "从零", "所有数据", "整张表", "全量", "全表", "重置"]


@dataclass
class Step:
    idx: int
    nl: str
    dsl: dict
    kind: str
    rows: int
    cols: int
    scope: str
    trace: list
    audit: dict
    is_detail: bool = False


class QuerySession:
    def __init__(self, data_path=None, reg: FieldRegistry | None = None, use_cache: bool = True,
                 engine: str = "pandas"):
        self.reg = reg or FieldRegistry.load()
        self.data_path = data_path or find_default_data()
        self.full_df = load_store_data(self.data_path, reg=self.reg, use_cache=use_cache)
        self.current: pd.DataFrame = self.full_df   # 门店级工作集（始终是 full_df 的行子集，列完整）
        self.steps: list[Step] = []
        self.engine = engine   # 计算后端：pandas（默认）| datafusion（可选逃生舱）
        # 第 10 块：DM 汇总层。不存在/过期自动重建（约 3s）；失败为 None → 全部走底表扫描
        self.dm_db: str | None = ensure_dm(self.data_path)

    # ---------- scope 判定 ----------
    def _scope_of(self, text: str) -> str:
        if any(w in text for w in _RESET_WORDS):
            return "full"
        if self.steps and any(w in text for w in _STAY_WORDS):
            return "current"
        return "full"

    # ---------- 纯计算（不修改状态、不写记忆） ----------
    def compute(self, text: str, src: pd.DataFrame, scope: str = "full",
                scope_label: str | None = None, verbose: bool = False,
                dsl: dict | None = None, engine: str | None = None) -> dict:
        """在给定 src 上执行一条查询，返回结构化结果 dict，但**不**修改
        self.current、也**不**写记忆。供 CLI 的 ask() 与 MCP 多会话管理复用。

        dsl：可选预解析好的 DSL；传入则跳过 auto_parse（用于应用记忆/纠偏 DSL）。

        返回 dict 含：ok / error / dsl / kind / rows / cols / columns /
        trace / audit / time_s / is_detail / out(DataFrame) / scope。
        """
        t0 = time.time()
        q = dsl if dsl is not None else auto_parse(text, self.reg)
        if verbose and scope_label:
            print(explain_query(text, self.reg, src, scope_label=scope_label))
        try:
            # DM 路由仅在全量数据上放行：工作集子集的分布必须现算（DM 是全量口径）
            dm = self.dm_db if src is self.full_df else None
            out, trace, kind, audit = _eng_run_query(src, q, self.reg,
                                                      engine=engine or self.engine,
                                                      dm_db=dm)
        except Exception as e:
            if verbose:
                print("✗ 执行失败（已在编译期/运行期拦截）:", e)
            return {"ok": False, "error": str(e), "out": None, "dsl": q,
                    "scope": scope, "is_detail": False}
        is_detail = (q.get("type") == "A" and not q.get("group_by"))
        res: dict[str, Any] = {
            "ok": True, "nl": text, "scope": scope, "kind": kind,
            "rows": len(out), "cols": out.shape[1],
            "columns": list(out.columns), "trace": trace,
            "audit": audit, "time_s": round(time.time() - t0, 2),
            "is_detail": is_detail, "out": out, "dsl": q,
        }
        if verbose:
            for t in trace:
                print(" •", t)
            print(f"耗时 {res['time_s']}s  工作集 {len(src):,} 行"
                  f"  scope={scope}  is_detail={is_detail}")
        return res

    # ---------- DSL 解析：用户纠偏 > 历史成功复用 > 重新解析 ----------
    def _resolve_dsl(self, text: str) -> tuple[dict, str]:
        """决定最终 DSL 来源：
        1) 用户纠偏（corrections，相似度≥0.50）优先 —— 这是 Vanna 纠偏飞轮的闭环；
        2) 历史高相似成功查询（≥0.85）直接复用，跳过重复解析；
        3) 否则重新 auto_parse。
        返回 (dsl, used) where used ∈ {correction, recall, parse}。
        """
        from memory import QueryMemory
        mem = QueryMemory()
        corr = mem.corrections(text, k=1, threshold=0.50)
        if corr:
            # 用户纠偏是**显式权威**，最高优先。它只能被用户再次 :fix 同一问题
            # 替换（memory.correct 已按 nl 去重），不会被一次普通提问静默推翻。
            return corr[0]["dsl"], "correction"
        hit = mem.best_match_dsl(text)  # 默认 ≥0.82
        if hit:
            return hit, "recall"
        return auto_parse(text, self.reg), "parse"

    # ---------- 多步主入口（CLI） ----------
    def ask(self, text: str, out_csv=None, verbose: bool = True,
            use_memory: bool = True) -> dict:
        """执行一条自然语言查询（支持多步连续追问）。

        返回结构化结果 dict；verbose=True 时打印 dry-plan 与执行 trace。
        成功查询会写入 RAG 记忆（自学飞轮）；命中纠偏/复用记忆时会优先采用。
        """
        from memory import QueryMemory

        scope = self._scope_of(text)
        src = self.current if scope == "current" else self.full_df
        label = (f"当前工作集 {len(src):,} 行" if scope == "current"
                 else f"全量数据 {len(src):,} 行") if verbose else None

        q, used = self._resolve_dsl(text)
        res = self.compute(text, src, scope=scope, scope_label=label, verbose=verbose, dsl=q)
        if not res["ok"]:
            return res
        out = res["out"]
        q = res["dsl"]

        if verbose and used != "parse":
            tag = "用户纠偏" if used == "correction" else "记忆复用"
            print(f"  ↳ DSL 来源：{tag}（跳过重新解析）")

        # 更新工作集：仅 A 类明细（无 group_by）成为新工作集。
        # out.index 保留了在 src 中的原始行位置，可还原完整列的门店明细。
        if res["is_detail"] and len(out):
            self.current = src.loc[out.index].copy()

        step = Step(idx=len(self.steps) + 1, nl=text, dsl=q, kind=res["kind"],
                    rows=res["rows"], cols=res["cols"], scope=scope,
                    trace=res["trace"], audit=res["audit"], is_detail=res["is_detail"])
        self.steps.append(step)

        result: dict[str, Any] = {
            "ok": True, "nl": text, "scope": scope, "kind": res["kind"],
            "rows": res["rows"], "cols": res["cols"],
            "columns": res["columns"], "trace": res["trace"],
            "audit": res["audit"], "time_s": res["time_s"],
            "is_detail": res["is_detail"], "out": out,
        }
        if out_csv:
            out.to_csv(out_csv, index=False)
            result["csv"] = str(out_csv)

        if verbose:
            print(f"耗时 {result['time_s']}s  工作集现为 {len(self.current):,} 行"
                  f"  scope={scope}  is_detail={res['is_detail']}")

        # RAG 记忆：仅在「真正重新解析」时写入成功查询。
        # 命中纠偏/复用记忆时（used != parse）**不写**——否则会把复用的 DSL
        # 当成新成功查询写回 success 库，造成自我复制污染（且让纠偏无法被替换）。
        if use_memory and used == "parse":
            try:
                QueryMemory().record(text, q, res["rows"], res["cols"], scope)
            except Exception:
                pass
        return result

    # ---------- 用户纠偏闭环（Vanna 飞轮真正闭合） ----------
    def fix_last(self, correct_nl: str | None = None, dsl: dict | None = None,
                 verbose: bool = True) -> dict:
        """针对最近一条查询做纠偏：用『正确意图』重新解析并在**同一作用域**执行，
        把『上一条自然语言 → 正确 DSL』写入记忆飞轮，立即展示修正结果。

        - correct_nl: 用户给出正确意图的自然语言（重新解析为 DSL）
        - dsl: 直接给定正确 DSL（:fix dsl <json> 形态），优先级高于 correct_nl

        闭环逻辑：之后用户再问同类问题，_resolve_dsl 会优先命中这条纠偏。
        """
        from memory import QueryMemory

        if not self.steps:
            return {"ok": False, "error": "没有可修正的上一条查询"}
        last = self.steps[-1]
        if dsl is not None:
            q = dsl
        elif correct_nl:
            q = auto_parse(correct_nl, self.reg)
        else:
            return {"ok": False, "error": "需提供 correct_nl 或 dsl"}

        src = self.current if last.scope == "current" else self.full_df
        label = (f"当前工作集 {len(src):,} 行" if last.scope == "current"
                 else f"全量数据 {len(src):,} 行") if verbose else None
        res = self.compute(correct_nl or "（直接 DSL 修正）", src, scope=last.scope,
                           scope_label=label, verbose=verbose, dsl=q)
        if not res["ok"]:
            return res
        out = res["out"]
        if res["is_detail"] and len(out):
            self.current = src.loc[out.index].copy()

        # 用正确 DSL 覆盖 last 的 dsl，并把 (上一条 nl → 正确 DSL) 写入纠偏记忆。
        # 注意：纠偏必须按『原始 phrasing』建索引，这样用户下次重复原话时才会被修正。
        orig_nl = last.nl
        last.dsl = q
        if correct_nl:
            last.nl = correct_nl
        QueryMemory().correct(orig_nl, q)
        res["fixed"] = True
        res["fixed_from"] = orig_nl
        if verbose:
            print(f"✓ 已记录纠偏：『{orig_nl}』→ 正确 DSL 已写入记忆飞轮")
        return res

    # ---------- 会话控制 ----------
    def reset(self):
        self.current = self.full_df
        self.steps = []

    def history(self) -> str:
        if not self.steps:
            return "（无历史步骤）"
        lines = []
        for s in self.steps:
            tail = " → 已成为工作集" if s.is_detail else ""
            lines.append(f"{s.idx}. [{s.scope}] {s.nl}  → {s.rows:,}行/{s.cols}列 ({s.kind}){tail}")
        return "\n".join(lines)
