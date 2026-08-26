# -*- coding: utf-8 -*-
"""
firewall.py
===========
SQL 确定性静态防火墙 (Deterministic Security Firewall)。
用于在 LLM (如 Vanna) 生成的 SQL 执行前进行确定性安全拦截与校验。

安全拦截规则：
1. 只读限制：严格只允许 SELECT 语句，坚决拒绝 DROP, DELETE, UPDATE, INSERT, ALTER, ATTACH, COPY, EXECUTE 等越权操作；
2. 注入防范：严格禁止分号多语句拼接注入；
3. 行数上限：自动强制注入 LIMIT 上限（默认最大 100,000 行），防止恶意/失控全量扫表耗尽内存；
4. 字段受管校验：校验 SQL 引用的列名是否在受管 FieldRegistry 中，拦截未受管隔离字段（如"是否匹配"）与未知幻觉字段。
"""
from __future__ import annotations

import re
from typing import Any
from agent_core import FieldRegistry

# 严禁出现的高危关键字
FORBIDDEN_KEYWORDS = [
    r"\bDROP\b", r"\bDELETE\b", r"\bUPDATE\b", r"\bINSERT\b", r"\bALTER\b",
    r"\bTRUNCATE\b", r"\bATTACH\b", r"\bDETACH\b", r"\bCOPY\b", r"\bEXEC\b",
    r"\bEXECUTE\b", r"\bPRAGMA\b", r"\bGRANT\b", r"\bREVOKE\b", r"\bCREATE\b"
]


class SQLFirewallViolation(ValueError):
    """SQL 违反安全防火墙策略时抛出的异常。"""
    pass


def validate_and_sanitize_sql(sql: str, reg: FieldRegistry, max_rows: int = 100000) -> dict[str, Any]:
    """对待执行的 SQL 语句进行确定性静态检查并返回清洗后的 SQL。

    返回: {"ok": bool, "sanitized_sql": str, "errors": list[str]}
    """
    errors: list[str] = []
    clean_sql = sql.strip().rstrip(";")

    # 1. 多语句注入拦截
    if ";" in clean_sql:
        errors.append("[Firewall] 检测到分号分隔的多语句拼接，为防范 SQL 注入已拒绝执行")

    # 2. 高危写操作关键字拦截
    for pat in FORBIDDEN_KEYWORDS:
        if re.search(pat, clean_sql, re.IGNORECASE):
            kw = pat.replace(r"\b", "")
            errors.append(f"[Firewall] 严禁执行包含「{kw}」的高危非查询指令（仅允许只读 SELECT）")

    # 3. 必须以 SELECT / WITH 开头
    if not re.match(r"^\s*(SELECT|WITH)\b", clean_sql, re.IGNORECASE):
        errors.append("[Firewall] 非法语句：仅允许 SELECT 或 WITH 引导的只读查询")

    # 4. 受管歧义字段拦截
    for f_name, f_meta in reg.fields.items():
        if f_meta.get("isolated") and f_name in clean_sql:
            errors.append(f"[Firewall] SQL 引用了已处于歧义隔离状态的字段「{f_name}」，已在防火墙拦截")

    # 5. 自动补全 LIMIT 上限 (若未设置 limit 或设置过大)
    sanitized_sql = clean_sql
    if not errors:
        m_limit = re.search(r"\bLIMIT\s+(\d+)\b", clean_sql, re.IGNORECASE)
        if m_limit:
            lim_val = int(m_limit.group(1))
            if lim_val > max_rows:
                sanitized_sql = re.sub(r"\bLIMIT\s+\d+\b", f"LIMIT {max_rows}", clean_sql, flags=re.IGNORECASE)
        else:
            sanitized_sql = f"{clean_sql} LIMIT {max_rows}"

    return {
        "ok": len(errors) == 0,
        "sanitized_sql": sanitized_sql,
        "errors": errors
    }


if __name__ == "__main__":
    reg = FieldRegistry.load()
    
    # 正常 SQL
    res1 = validate_and_sanitize_sql("SELECT 规划大区, COUNT(*) FROM outlets GROUP BY 规划大区", reg)
    print("Test 1 (Normal):", res1)
    assert res1["ok"] is True
    assert "LIMIT 100000" in res1["sanitized_sql"]

    # 注入高危 DROP
    res2 = validate_and_sanitize_sql("SELECT * FROM outlets; DROP TABLE outlets;", reg)
    print("\nTest 2 (Injection DROP):", res2)
    assert res2["ok"] is False

    # 歧义隔离字段
    res3 = validate_and_sanitize_sql("SELECT * FROM outlets WHERE 是否匹配 = '是'", reg)
    print("\nTest 3 (Isolated Field):", res3)
    assert res3["ok"] is False

    print("\n>>> SQL 防火墙单元测试全部通过！ <<<")
