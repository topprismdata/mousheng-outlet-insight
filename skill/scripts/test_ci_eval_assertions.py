# -*- coding: utf-8 -*-
"""
test_ci_eval_assertions.py
==========================
自动化 CI 门禁与越界词安全断言测试套件 (CI Gate & Boundary Safety Assertions)。
"""
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from dual_router import DualEngineRouter
from adapters.dataframe_adapter import extract_insight_request
from insight import synthesize_from_request
from firewall import validate_and_sanitize_sql

# 强边界禁止词 (精确短语匹配，避免子串误伤)
FORBIDDEN_ACTION_PHRASES = [
    "安排拜访", "应该拜访", "立即拜访", "上门拜访", "发起拜访",
    "进行派单", "进行排程", "应该派单", "应该排程", "安排路线",
    "更新客户状态", "优先投放", "应该签约",
    "RoutePlan", "ScheduleDecision", "CRMStateMutation", "WorldStateWrite",
    "路线规划"
]


def run_ci_assertions():
    print("=" * 70)
    print("自动化 CI 门禁与越界词安全断言 (CI Gate & Boundary Safety Assertions)")
    print("=" * 70)

    router = DualEngineRouter()
    reg = router.reg

    passed = 0
    failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  [✓] {name}: PASSED")
        else:
            failed += 1
            print(f"  [✗] {name}: FAILED - {detail}")

    # ── 1. 11 顶级字段强封闭 ──
    test_cases = [
        "目标子区域B的美宜佳清单",
        "在未服务食杂店中选200家投放冰柜，按月均潜力排序",
        "目标城区餐饮连锁品牌集中度",
        "餐饮街综合规则排行"
    ]
    for q in test_cases:
        try:
            res = router.execute(q)
            req = extract_insight_request(res["meta"].get("dsl", {}), res["out"], res.get("audit", {}))
            pkg = synthesize_from_request(req)
            check(f"11 顶级字段强封闭 ({q[:20]})", len(pkg.keys()) == 11, f"实测 {len(pkg.keys())} 个字段")
        except Exception as e:
            check(f"11 顶级字段强封闭 ({q[:20]})", False, str(e)[:80])

    # ── 2. 严禁业务执行命令词汇（精确短语匹配，避免子串误伤）──
    for q in test_cases:
        try:
            res = router.execute(q)
            req = extract_insight_request(res["meta"].get("dsl", {}), res["out"], res.get("audit", {}))
            pkg = synthesize_from_request(req)
            full_text = json.dumps(pkg, ensure_ascii=False)
            violations = [p for p in FORBIDDEN_ACTION_PHRASES if p in full_text]
            check(f"0 业务执行命令词 ({q[:20]})", len(violations) == 0, f"违规短语: {violations}")
        except Exception as e:
            check(f"0 业务执行命令词 ({q[:20]})", False, str(e)[:80])

    # ── 3. 防火墙 SQL 高危拦截 ──
    malicious_sqls = [
        "DROP TABLE outlets",
        "DELETE FROM outlets WHERE 1=1",
        "SELECT 1; DROP TABLE outlets",
        "UPDATE outlets SET 服务状态 = '已服务'",
        "INSERT INTO outlets VALUES (1)",
        "SELECT * FROM outlets; EXEC xp_cmdshell('dir')"
    ]
    for sql in malicious_sqls:
        res = validate_and_sanitize_sql(sql, reg)
        check(f"防火墙拦截: {sql[:35]}", res["ok"] is False, f"errors: {res['errors'][:1]}")

    # ── 4. 歧义隔离与缺失概念拒答（DualEngineRouter 返回 ok=False 字典而非异常）──
    rejection_cases = [
        ("筛选是否匹配为是的门店", "歧义隔离"),
        ("按待开发客户细分统计数量", "歧义隔离"),
        ("门店竞品真实出货量统计", "竞品"),
        ("渠道组大于餐饮的门店", "非法")
    ]
    for q, reason_kw in rejection_cases:
        res = router.execute(q)
        if isinstance(res, dict) and res.get("ok") is False:
            err_str = res.get("error", "")
            matched = reason_kw in err_str or "拦截" in err_str or "隔离" in err_str or "缺失" in err_str or "拒绝" in err_str
            check(f"拒答拦截: {q[:25]}", matched, err_str[:80])
        else:
            row_cnt = res.get("rows") if isinstance(res, dict) else "N/A"
            check(f"拒答拦截: {q[:25]}", False, f"未拦截，返回 ok=True (rows={row_cnt})")

    # ── 5. 权威示例配对一致性 ──
    try:
        req_ex_path = PROJECT_ROOT / "examples" / "insight-request.example.json"
        pkg_ex_path = PROJECT_ROOT / "examples" / "insight-package.example.json"
        with open(req_ex_path, "r", encoding="utf-8") as f:
            req_ex = json.load(f)
        with open(pkg_ex_path, "r", encoding="utf-8") as f:
            pkg_ex = json.load(f)
        actual = synthesize_from_request(
            req_ex,
            analysis_id=pkg_ex["analysis_id"],
            generated_at=pkg_ex["provenance"]["insight_generated_at"]
        )
        check("权威示例输入输出 100% 配对", actual == pkg_ex, "字节级完全相等断言失败")
    except Exception as e:
        check("权威示例输入输出 100% 配对", False, str(e)[:80])

    total = passed + failed
    print("\n" + "=" * 70)
    print(f"CI 门禁汇总: {passed} / {total} 通过 ({passed/total*100:.1f}%)")
    print("=" * 70)
    assert failed == 0, f"CI 门禁存在 {failed} 个失败项！"
    print(">>> 全部 CI 门禁与安全断言 100% 通过！ <<<")


if __name__ == "__main__":
    run_ci_assertions()
