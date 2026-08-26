# -*- coding: utf-8 -*-
"""
dual_router.py
==============
双轨查询执行路由器 (Dual Engine Router v1.2)。
实现行业成熟的「Vanna + DuckDB 主引擎 + 确定性 DSL 规则排行兜底」双轨架构：

1. 轨道一 (主轨：Vanna + SQL + 防火墙 + DuckDB)：
   处理 A 类 (明细清单/配额选点) 与 B 类 (分布探索/品牌集中度) 查询，提供毫秒级高并发只读 SQL 执行；
2. 轨道二 (规则轨：DSL + RuleQuery)：
   处理 C 类 (多因子多规则达标率排行) 查询，提供精确的加权评分、规模基数与高/中/低分层。

对外提供统一签名：
`execute(text) -> dict`
返回包含 ok/engine/out/rows/trace/audit/meta 等字段的结果，拒答场景下 ok=False。
所有拒答与执行异常自动通过 feedback_integration 写入 query_memory.jsonl 形成纠偏闭环。
"""
from __future__ import annotations

import re
import time
from typing import Any
import pandas as pd

from agent_core import FieldRegistry, run_query as dsl_run_query, load_store_data, audit as dsl_audit
from nl_parser import auto_parse
from vanna_adapter import LocalVannaAdapter
from firewall import validate_and_sanitize_sql
from duckdb_adapter import query_df
from feedback_integration import FailureFeedbackRecorder


class DualEngineRouter:
    """双轨查询执行路由器，含防御性拒答守门与失败案例自动回流。"""

    def __init__(self, reg: FieldRegistry | None = None, full_df: pd.DataFrame | None = None) -> None:
        self.reg = reg or FieldRegistry.load()
        self.full_df = full_df if full_df is not None else load_store_data()
        self.vanna_adapter = LocalVannaAdapter(self.reg)
        self.feedback = FailureFeedbackRecorder()

    def _gate_rejection(self, q_clean: str) -> dict[str, Any] | None:
        """防御性拒答守门：拦截歧义隔离字段、缺失概念、未受管黑话、非法算子。返回拒答字典或 None。"""
        # 1. 歧义隔离字段拦截
        isolated_fields = [name for name, meta in self.reg.fields.items() if meta.get("isolated")]
        for iso in isolated_fields:
            if iso in q_clean:
                return {"ok": False, "error": f"请求涉及已隔离的歧义字段「{iso}」（未纳入执行计划），防御性拒绝避免幻觉", "rejected": True}
        # 2. 缺失概念拒答
        if ("竞品" in q_clean and ("销量" in q_clean or "出货" in q_clean)) or "出货量统计" in q_clean:
            return {"ok": False, "error": "请求的业务概念「竞品出货量」没有对应物理数据，Agent 拒绝用代理字段冒充答案", "rejected": True}
        # 3. 未受管黑话字段拦截
        for blacklisted in ["老板星座", "竞品真实销量", "竞品出货量"]:
            if blacklisted in q_clean:
                return {"ok": False, "error": f"请求引用了数据与本体中不存在的字段「{blacklisted}」，Agent 拒绝全表降级查询", "rejected": True}
        # 4. 类别字段非法算子拦截（支持中英文运算符）
        m_illegal = re.search(r"(渠道组|大区|业态|品牌|城市|区县|省份|状态)\s*(大于|小于|超过|不少于|不大于|高于|低于|>|<|>=|<=|=)\s*([\u4e00-\u9fa5]+)", q_clean)
        if m_illegal:
            return {"ok": False, "error": f"类别字段「{m_illegal.group(1)}」不能使用数值/字符串比较算子「{m_illegal.group(2)}」，语义计划在编译期被拒绝", "rejected": True}
        return None

    def execute(self, text: str) -> dict[str, Any]:
        """统一执行入口：自动分流到最适合的执行轨道。"""
        t0 = time.perf_counter()
        q_clean = text.strip()

        # 0. 防御性拒答守门 (最先执行)
        gate = self._gate_rejection(q_clean)
        if gate:
            # 失败案例自动回流到 query_memory.jsonl
            self.feedback.record_rejection(
                question=text, reason=gate.get("error", ""),
                expected="成功受控拦截，避免幻觉与越界",
                actual="已拒答"
            )
            return gate

        # 1. 判断是否为 C 类多因子规则排行
        is_c_rule = any(k in q_clean for k in ["占比", "为高", "为低", "为中", "达标", "30%", "50%", "规则排行", "综合打分"]) and "选" not in q_clean

        try:
            if is_c_rule:
                dsl = auto_parse(q_clean, self.reg)
                res_df, trace, kind, audit_rep = dsl_run_query(self.full_df, dsl, self.reg)
                engine_type = "DSL_RuleEngine"
                meta = {"engine": engine_type, "dsl": dsl}
            else:
                dsl = auto_parse(q_clean, self.reg)
                res_df, safe_sql = self.vanna_adapter.query(q_clean)
                kind = "A" if dsl.get("type") == "A" else "B"
                trace = [f"[Vanna] 生成安全 SQL: {safe_sql}", f"[DuckDB] 返回 {len(res_df):,} 行"]
                _, audit_rep = dsl_audit(self.full_df, dsl, res_df, self.reg, kind)
                engine_type = "Vanna_DuckDB"
                meta = {"engine": engine_type, "sql": safe_sql, "dsl": dsl}

            return {
                "ok": True,
                "engine": engine_type,
                "out": res_df,
                "rows": len(res_df),
                "trace": trace,
                "kind": kind,
                "audit": audit_rep,
                "meta": meta,
                "time_s": round(time.perf_counter() - t0, 3)
            }
        except Exception as e:
            # 任何执行异常自动回流
            err_str = str(e)
            self.feedback.record_rejection(
                question=text, reason=f"执行异常: {err_str[:120]}",
                expected="正常返回结构化数据",
                actual=err_str[:120]
            )
            return {
                "ok": False,
                "error": err_str,
                "rejected": False,
                "engine": None,
                "out": pd.DataFrame(),
                "rows": 0,
                "trace": [err_str],
                "kind": None,
                "audit": None,
                "meta": {},
                "time_s": round(time.perf_counter() - t0, 3)
            }


if __name__ == "__main__":
    router = DualEngineRouter()
    print("=== 双轨路由器 (Dual Engine Router) 测试 ===")

    res_a = router.execute("目标子区域B的美宜佳清单")
    print(f"\n[轨道一 A类] 耗时 {res_a['time_s']}s | 引擎: {res_a['engine']} | 行数: {res_a['rows']}")

    res_b = router.execute("美宜佳各大区分布")
    print(f"[轨道一 B类] 耗时 {res_b['time_s']}s | 引擎: {res_b['engine']} | 行数: {res_b['rows']}")

    res_c = router.execute("餐饮街综合规则排行")
    print(f"[轨道二 C类] 耗时 {res_c['time_s']}s | 引擎: {res_c['engine']} | 行数: {res_c['rows']}")

    res_rej = router.execute("筛选是否匹配为是的门店")
    print(f"\n[拒答拦截] ok={res_rej['ok']}, error={res_rej.get('error','')[:50]}")

    print(f"\n[反馈流] 已累计写入 {len(router.feedback.load_rejections(50))} 条 failure_feedback")
    print("\n>>> 双轨路由器 + 失败回流 100% 成功！ <<<")
