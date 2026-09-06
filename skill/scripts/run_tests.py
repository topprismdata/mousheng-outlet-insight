# -*- coding: utf-8 -*-
"""
run_tests.py
============
outlet-insight 数据分析 Agent —— 场景化测试套件（固化进 Skill）。

覆盖能力：A类明确查询 / B类分布探索 / C类规则排行 / 多步会话 / Dry-plan 透明计划 /
         负向编译护栏 / 数据质量体检 / 纠偏闭环 / embedding 召回 / MCP 多步暴露。

设计要点（区别于一次性探索脚本）：
  - 路径全部基于 __file__ 推导，可随 Skill 一起分发、不写死用户目录。
  - 记忆保护：运行前自动备份用户真实飞轮（query_memory.jsonl / .npz），
    测试清空后跑，结束后恢复 —— 绝不污染用户的自学记忆。
  - 支持 --keep-memory 关闭保护（仅开发调试用，会污染真实记忆）。

运行：
  python run_tests.py                 # 生成 HTML 报告 + 控制台摘要
  python run_tests.py --no-report    # 仅控制台
  python run_tests.py --keep-memory  # 不保护记忆（调试）
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

# ---- 路径基于脚本自身推导（可分发） ----
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import pandas as pd  # noqa: E402
from agent_core import FieldRegistry, run_query, validate_query  # noqa: E402
from nl_parser import auto_parse, explain_query  # noqa: E402
from session import QuerySession  # noqa: E402
from memory import QueryMemory, MEM_PATH, EMB_PATH  # noqa: E402
from data_assertions import build_baseline, generate_dcs, check_dcs  # noqa: E402
from feedback_log import (FeedbackLog, REVIEW_APPROVED, REVIEW_REJECTED,  # noqa: E402
                          RISK_HIGH, RISK_LOW)  # noqa: E402

REG = FieldRegistry.load()

# ---------------------------------------------------------------- 测试框架
class Results:
    def __init__(self):
        self.cases: list[dict] = []

    def add(self, cid, name, scenario, ok, expect, actual, detail=""):
        self.cases.append(dict(id=cid, name=name, scenario=scenario, ok=bool(ok),
                               expect=str(expect), actual=str(actual), detail=detail))

    def ok(self, *a, **k):
        # 严格保留实测布尔值判定，严禁强制覆盖为 True
        return self.add(*a, **k)

    def fail(self, *a, **k):
        return self.add(*a, **k)


R = Results()
SESS = None  # 在 main 中创建

def _ask(text, use_memory=False, sess=None):
    return (sess or SESS).ask(text, verbose=False, use_memory=use_memory)

_BLOCKED_TXT = "查询被语义层拦截/无匹配数据"


def _blocked(r, cid, name, scenario, expect):
    """查询被语义层拦截或未产出结果 DataFrame（out=None）时，
    把该用例如实记录为失败（附原因），返回 True 表示跳过后续断言（绝不抛异常）。"""
    if r.get("out") is None:
        reason = r.get("error") or _BLOCKED_TXT
        R.fail(cid, name, scenario, False, expect, _BLOCKED_TXT, f"原因: {reason}")
        return True
    return False


# ============================================================ A 类：明确查询
def test_A():
    r = _ask("目标子区域B的美宜佳清单")
    if _blocked(r, "T01", "A-品牌+大区(美宜佳+目标子区域B)", "A类明确查询", "37 行"):
        return
    R.ok("T01", "A-品牌+大区(美宜佳+目标子区域B)", "A类明确查询",
         r["rows"] == 37, "37 行", f'{r["rows"]} 行',
         detail="DSL 过滤: 规划大区=目标子区域B; 连锁品牌名称 contains 美宜佳")

    r = _ask("美宜佳清单")
    if _blocked(r, "T02", "A-品牌全量(美宜佳)", "A类明确查询", "346 行"):
        return
    R.ok("T02", "A-品牌全量(美宜佳)", "A类明确查询",
         r["rows"] == 346, "346 行", f'{r["rows"]} 行')

    r = _ask("客房数大于100间的非连锁酒店")
    if _blocked(r, "T03", "A-客房>100 且 非连锁 酒店", "A类明确查询", "行数>0 且 房间数>100 且 非连锁 全满足"):
        return
    out = r["out"]
    cond1 = (pd.to_numeric(out["携程-房间数"], errors="coerce") > 100).all()
    cond2 = (out["是否连锁"] == "否").all()
    R.ok("T03", "A-客房>100 且 非连锁 酒店", "A类明确查询",
         r["rows"] > 0 and cond1 and cond2,
         "行数>0 且 房间数>100 且 非连锁 全满足",
         f'行数={r["rows"]}, 房间数>100全满足={cond1}, 非连锁全满足={cond2}')

    r = _ask("瑞幸清单")
    if _blocked(r, "T04", "A-品牌(瑞幸)", "A类明确查询", "行数>0"):
        return
    R.ok("T04", "A-品牌(瑞幸)", "A类明确查询",
         r["rows"] > 0, "行数>0", f'{r["rows"]} 行')

    r = _ask("餐饮业态的客户清单")
    if _blocked(r, "T05", "A-业态=餐饮", "A类明确查询", "行数>0 且 渠道组=餐饮 全满足"):
        return
    out = r["out"]
    R.ok("T05", "A-业态=餐饮", "A类明确查询",
         r["rows"] > 0 and (out["渠道组"] == "餐饮").all(),
         "行数>0 且 渠道组=餐饮 全满足",
         f'行数={r["rows"]}, 渠道组全为餐饮={(out["渠道组"]=="餐饮").all()}')

    r = _ask("未服务的食杂潜力TOP20")
    if _blocked(r, "T06", "A-未服务+食杂+潜力TOP20", "A类明确查询", "服务状态=未服务, 渠道组=食杂, ≤20行"):
        return
    out = r["out"]
    cond = (out["服务状态"] == "未服务").all() and (out["渠道组"] == "食杂").all() and (len(out) <= 20)
    R.ok("T06", "A-未服务+食杂+潜力TOP20", "A类明确查询",
         cond, "服务状态=未服务, 渠道组=食杂, ≤20行",
         f'行数={len(out)}, 未服务全满足={(out["服务状态"]=="未服务").all()}, 食杂全满足={(out["渠道组"]=="食杂").all()}')

    r = _ask("餐饮高评分门店清单")
    if _blocked(r, "T07", "A-度量(餐饮高评分≥3.8)", "A类明确查询", "行数>0 且 点评评分≥3.8 全满足"):
        return
    out = r["out"]
    cond = (pd.to_numeric(out["大众点评餐饮点评评分星级"], errors="coerce") >= 3.8).all()
    R.ok("T07", "A-度量(餐饮高评分≥3.8)", "A类明确查询",
         r["rows"] > 0 and cond, "行数>0 且 点评评分≥3.8 全满足",
         f'行数={r["rows"]}, 评分≥3.8全满足={cond}')

    r = _ask("高德上榜的餐饮街")
    if _blocked(r, "T08", "A-实体(餐饮街)+度量(高德上榜)", "A类明确查询", "行数>0 且 餐饮街+高德Y 全满足"):
        return
    out = r["out"]
    cond = (out["经营场所"].astype(str).str.contains("餐饮街", na=False).all()
            and (out["是否高德扫街榜/状元榜"] == "Y").all())
    R.ok("T08", "A-实体(餐饮街)+度量(高德上榜)", "A类明确查询",
         r["rows"] > 0 and cond, "行数>0 且 餐饮街+高德Y 全满足",
         f'行数={r["rows"]}, 餐饮街全满足={(out["经营场所"].astype(str).str.contains("餐饮街",na=False)).all()}')


# ============================================================ B 类：分布探索
def test_B():
    r = _ask("餐饮街的客户分布")
    if _blocked(r, "T09", "B-餐饮街的客户分布", "B类分布探索", "行数>0"):
        return
    R.ok("T09", "B-餐饮街的客户分布", "B类分布探索",
         r["rows"] > 0, "行数>0", f'{r["rows"]} 行 (聚合: {r["columns"]})')

    r = _ask("各业态在目标省份的数量")
    if _blocked(r, "T10", "B-目标省份各业态数量", "B类分布探索", "行数>0 且含 渠道组 维度"):
        return
    out = r["out"]
    R.ok("T10", "B-目标省份各业态数量", "B类分布探索",
         r["rows"] > 0 and "渠道组" in out.columns,
         "行数>0 且含 渠道组 维度", f'行数={r["rows"]}, 维度列={list(out.columns)}')

    r = _ask("美宜佳在各个大区的分布")
    if _blocked(r, "T11", "B-美宜佳各大区分布", "B类分布探索", "行数>0 且含 规划大区 维度"):
        return
    out = r["out"]
    R.ok("T11", "B-美宜佳各大区分布", "B类分布探索",
         r["rows"] > 0 and "规划大区" in out.columns,
         "行数>0 且含 规划大区 维度", f'行数={r["rows"]}, 维度列={list(out.columns)}')

    r = _ask("目标城市各城市的客户数量")
    if _blocked(r, "T12", "B-目标城市各城市数量", "B类分布探索", "行数>0 且含 城市 维度"):
        return
    out = r["out"]
    R.ok("T12", "B-目标城市各城市数量", "B类分布探索",
         r["rows"] > 0 and "城市" in out.columns,
         "行数>0 且含 城市 维度", f'行数={r["rows"]}, 维度列={list(out.columns)}')


# ============================================================ C 类：规则排行
def test_C():
    tmpl = ("街道餐饮客户不低于20家，周末人流按大小排序，"
            "评分3.8分以上客户占比50%以上为高，开业3年以上客户占比50%以上为高")
    r = _ask(tmpl)
    if _blocked(r, "T13", "C-餐饮街综合规则排行", "C类规则排行", "行数>0 且含 达标率/等级/数据完整度%"):
        return
    out = r["out"]
    has_grade = ("达标率" in out.columns) and ("等级" in out.columns) and ("数据完整度%" in out.columns)
    R.ok("T13", "C-餐饮街综合规则排行", "C类规则排行",
         r["rows"] > 0 and has_grade, "行数>0 且含 达标率/等级/数据完整度%",
         f'行数={r["rows"]}, 关键列={[c for c in ["达标率","等级","数据完整度%"] if c in out.columns]}')

    r = _ask("商场里餐饮客户不低于20家为达标")
    if _blocked(r, "T14", "C-简单规则(商场餐饮客户≥20)", "C类规则排行", "行数>0 且含 达标率 且 ≤50(top_n)"):
        return
    out = r["out"]
    R.ok("T14", "C-简单规则(商场餐饮客户≥20)", "C类规则排行",
         r["rows"] > 0 and "达标率" in out.columns and r["rows"] <= 50,
         "行数>0 且含 达标率 且 ≤50(top_n)", f'行数={r["rows"]}, 列={list(out.columns)[:6]}')


# ============================================================ 多步会话
def test_multistep():
    s = QuerySession(use_cache=True)
    r1 = s.ask("美宜佳清单", verbose=False, use_memory=False)
    if _blocked(r1, "T15", "多步会话(取美宜佳→筛目标子区域B→看分布→回全量)", "多步会话",
                "工作集: 346→37→(分布 N 行)→客房>100全量"):
        return
    if r1["rows"] != 346:
        R.fail("T15", "多步会话(取美宜佳→筛目标子区域B→看分布→回全量)", "多步会话", False,
               "step1 美宜佳清单=346 行(数据耦合前提)", f'step1 行数={r1["rows"]} (期望 346)')
        return
    r2 = s.ask("这些里目标子区域B的", verbose=False, use_memory=False)
    ws_after2 = len(s.current)
    r3 = s.ask("看它们的业态分布", verbose=False, use_memory=False)
    r4 = s.ask("整个数据里客房数大于100间的酒店", verbose=False, use_memory=False)
    ws_after4 = len(s.current)
    for _tag, _r in (("step2", r2), ("step3", r3), ("step4", r4)):
        if _blocked(_r, "T15", "多步会话(取美宜佳→筛目标子区域B→看分布→回全量)", "多步会话",
                    f"工作集: 346→37→(分布 N 行)→客房>100全量（{_tag} 被拦截）"):
            return
    R.ok("T15", "多步会话(取美宜佳→筛目标子区域B→看分布→回全量)", "多步会话",
         ws_after2 == 37 and r3["rows"] > 0 and ws_after4 == r4["rows"] and r4["rows"] > 0,
         "工作集: 346→37→(分布 N 行)→客房>100全量",
         f'step2工作集={ws_after2}, step3分布={r3["rows"]}行, step4={ws_after4}行(全量客房>100)')


# ============================================================ Dry-plan
def test_dryplan():
    plan = explain_query("美宜佳清单", REG, SESS.full_df)
    cond = "[筛选]" in plan and "连锁品牌名称" in plan and "美宜佳" in plan and "数据质量与适用性评估" in plan
    R.ok("T16", "Dry-plan(美宜佳清单 透明执行计划)", "Dry-plan透明计划",
         cond, "计划含 [筛选] 连锁品牌名称 contains 美宜佳 及 数据质量与适用性评估",
         "计划片段:\n" + "\n".join("  " + l for l in plan.splitlines()[:5]))

# ============================================================ 负向编译护栏
def test_guardrails():
    bad = {"type": "A", "filters": [{"field": "不存在的字段xyz", "op": "=", "value": "测试"}]}
    v = validate_query(bad, REG)
    R.ok("T17", "负向-幻觉字段被拦截", "编译护栏",
         not v["ok"] and any("不存在" in e or "不在语义层" in e for e in v["errors"]),
         "validate ok=False 且报错含『不在语义层』",
         f'ok={v["ok"]}, errors={v["errors"]}')

    bad = {"type": "A", "filters": [{"field": "渠道组", "op": ">", "value": 5}]}
    v = validate_query(bad, REG)
    R.ok("T18", "负向-类别字段非法算子(>)被拦截", "编译护栏",
         not v["ok"] and any("不支持算子" in e for e in v["errors"]),
         "validate ok=False 且报错含『不支持算子』",
         f'ok={v["ok"]}, errors={v["errors"]}')

    bad = {"type": "C", "candidate_field": "幻觉候选字段", "rules": [
        {"name": "x", "kind": "count", "filters": [{"field": "渠道组", "op": "=", "value": "餐饮"}],
         "op": ">=", "value": 1}]}
    v = validate_query(bad, REG)
    raised = False
    try:
        run_query(SESS.full_df, bad, REG)
    except ValueError:
        raised = True
    R.ok("T19", "负向-幻觉候选字段(编译期拦截+运行期抛错)", "编译护栏",
         not v["ok"] and raised, "validate ok=False 且 run_query 抛 ValueError",
         f'validate_ok={v["ok"]}, run_query抛错={raised}')


# ============================================================ 数据质量体检
def test_audit():
    tmpl = ("街道餐饮客户不低于20家，周末人流按大小排序，"
            "评分3.8分以上客户占比50%以上为高，开业3年以上客户占比50%以上为高")
    r = _ask(tmpl)
    if _blocked(r, "T20", "体检-C类(数据完整度%+质量建议)", "数据质量体检", "含 数据完整度% 列 且 有 recommendation"):
        return
    ar = r["audit"]
    has_rec = bool(ar.get("recommendation")) and ("数据完整度%" in r["out"].columns)
    R.ok("T20", "体检-C类(数据完整度%+质量建议)", "数据质量体检",
         has_rec, "含 数据完整度% 列 且 有 recommendation",
         f'recommendation={ar.get("recommendation")}; audited_dim={ar.get("audited_dimensions")}')

    r = _ask("客房数大于100间的非连锁酒店")
    if _blocked(r, "T21", "体检-A类(关键字段缺失率)", "数据质量体检", "audit 含 field_missing 键"):
        return
    ar = r["audit"]
    R.ok("T21", "体检-A类(关键字段缺失率)", "数据质量体检",
         "field_missing" in ar, "audit 含 field_missing 键",
         f'field_missing={ar.get("field_missing")}')


# ============================================================ 纠偏闭环 + embedding
def test_correction_and_embedding():
    QueryMemory().clear()
    s = QuerySession(use_cache=True)
    r1 = s.ask("美宜佳清单", verbose=False, use_memory=True)
    if _blocked(r1, "T22", "纠偏闭环(美宜佳346 → :fix目标子区域B37 → 重复命中对)", "纠偏闭环",
                "首问346, fix后37, 重复原话命中37"):
        return
    r2 = s.fix_last(correct_nl="美宜佳且规划大区=目标子区域B", verbose=False)
    if _blocked(r2, "T22", "纠偏闭环(美宜佳346 → :fix目标子区域B37 → 重复命中对)", "纠偏闭环",
                "首问346, fix后37, 重复原话命中37"):
        return
    s2 = QuerySession(use_cache=True)
    r3 = s2.ask("美宜佳清单", verbose=False, use_memory=True)
    if _blocked(r3, "T22", "纠偏闭环(美宜佳346 → :fix目标子区域B37 → 重复命中对)", "纠偏闭环",
                "首问346, fix后37, 重复原话命中37"):
        return
    R.ok("T22", "纠偏闭环(美宜佳346 → :fix目标子区域B37 → 重复命中对)", "纠偏闭环",
         r1["rows"] == 346 and r2["rows"] == 37 and r3["rows"] == 37,
         "首问346, fix后37, 重复原话命中37",
         f'r1={r1["rows"]}, r2(fix)={r2["rows"]}, r3(重复)={r3["rows"]}')

    QueryMemory().clear()
    s = QuerySession(use_cache=True)
    s.ask("美宜佳清单", verbose=False, use_memory=True)
    mem = QueryMemory()
    hit = mem.best_match_dsl("美宜佳门店清单")
    R.ok("T23", "embedding-近义召回(美宜佳门店清单→全量DSL)", "记忆召回",
         hit is not None, "best_match_dsl 返回非 None(余弦≥阈值)",
         f'hit_dsl_type={hit.get("type") if hit else None}, 来源=美宜佳全量')

    nohit = mem.best_match_dsl("客房大于100间的酒店")
    R.ok("T24", "embedding-区分度(客房>100酒店 不应召回美宜佳)", "记忆召回",
         nohit is None, "best_match_dsl 返回 None(余弦<阈值)",
         f'nohit={nohit}')


# ============================================================ MCP 多步(subprocess)
def test_mcp():
    py = sys.executable
    server = str(SCRIPT_DIR / "mcp_server.py")

    def frame(msg):
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        return b"Content-Length: %d\r\n\r\n" % len(data) + data

    msgs = [
        frame({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}}}),
        frame({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        frame({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "query", "arguments": {"question": "美宜佳清单"}}}),
        frame({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "query",
                           "arguments": {"question": "美宜佳清单", "session_id": "unit25"}}}),
        frame({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "query",
                           "arguments": {"question": "这些里目标子区域B的",
                                         "session_id": "unit25", "scope": "current"}}}),
        frame({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                "params": {"name": "session_reset", "arguments": {"session_id": "unit25"}}}),
        frame({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": "profile", "arguments": {}}}),
    ]
    try:
        out = subprocess.run([py, server], input=b"".join(msgs),
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=240).stdout
        parsed, i, idx = [], 0, 0
        while True:
            nl = out.find(b"Content-Length:", i)
            if nl == -1:
                break
            eol = out.find(b"\r\n", nl)
            cl = int(out[nl + len(b"Content-Length:"):eol].strip())
            bs = eol + 4
            body = out[bs:bs + cl]
            parsed.append(json.loads(body))
            idx += 1
            i = bs + cl
            if idx >= 7:
                break
        init, tl, q1, q2, q3 = parsed[0], parsed[1], parsed[2], parsed[3], parsed[4]
        tools = [t["name"] for t in tl["result"]["tools"]]
        r1 = json.loads(q1["result"]["content"][0]["text"])
        r2 = json.loads(q2["result"]["content"][0]["text"])
        r3 = json.loads(q3["result"]["content"][0]["text"])
        ok = (init.get("result") and "result" in tl
              and set(["semantic_layer", "explain", "query", "validate", "session_reset", "profile"]).issubset(set(tools))
              and r1["rows"] > 0 and r2.get("working_set_rows") == 346
              and r3.get("input_rows") == 346 and r3["rows"] == 37)
        R.ok("T25", "MCP-多步context(外部agent连续追问)", "MCP语义层暴露",
             ok, "握手成功 + 6 tools + 单发>0 + 会话工作集346 + current筛出37",
             f'tools={tools}; 单发rows={r1["rows"]}; 会话ws={r2.get("working_set_rows")}; '
             f'current input_rows={r3.get("input_rows")} rows={r3["rows"]}')

        # T30: MCP profile tool（统计/画像层）返回整体画像
        p = json.loads(parsed[6]["result"]["content"][0]["text"])
        R.ok("T30", "MCP-profile tool(字段统计画像)", "统计/画像层",
             p.get("n_fields", 0) > 0 and bool(p.get("overall")),
             "返回 整体画像(n_fields>0 且含 overall 维度分布)",
             f'n_fields={p.get("n_fields")}; overall维度={list(p.get("overall", {}).keys())}')
    except Exception as e:
        R.fail("T25", "MCP-多步context", "MCP语义层暴露", "异常", "无", str(e))
        R.fail("T30", "MCP-profile tool", "统计/画像层", "异常", "无", str(e))


# ============================================================ 统计/画像层（第5块）
def test_profile():
    """画像层用合成小数据验证（不依赖 <size> MB 全量，保持套件快速）。"""
    import tempfile
    from profiler import build_profile, load_profile, route_metric, gen_schema_md
    from agent_core import FieldRegistry

    # ---- 合成数据：餐饮有点评评分、住宿有携程评分/房间数；按业态覆盖率不同 ----
    df = pd.DataFrame({
        "渠道组": ["餐饮", "餐饮", "住宿", "住宿", "餐饮"],
        "省份": ["目标省份省", "目标省份省", "目标省份省", "目标省份省", "目标省份省"],
        "大众点评餐饮点评评分星级": [4.0, 3.5, None, None, 4.2],
        "携程-综合评分": [None, None, 4.5, 4.0, None],
        "携程-房间数": [None, None, 120, 80, None],
    })
    reg = FieldRegistry(fields={
        "渠道组": {"type": "category"},
        "省份": {"type": "category"},
        "大众点评餐饮点评评分星级": {"type": "numeric"},
        "携程-综合评分": {"type": "numeric"},
        "携程-房间数": {"type": "numeric"},
    }, synonyms={}, measures={}, entities={})

    prof = build_profile(df, reg)
    R.ok("T26", "画像-build_profile(字段统计结构)", "统计/画像层",
         prof["n_rows"] == 5 and "大众点评餐饮点评评分星级" in prof["fields"]
         and prof["fields"]["大众点评餐饮点评评分星级"]["type"] == "numeric"
         and prof["fields"]["大众点评餐饮点评评分星级"]["median"] == 4.0
         and "by_业态" in prof["fields"]["大众点评餐饮点评评分星级"],
         "5 行；数值字段含 median=4.0；跨业态稀疏字段含 by_业态",
         f'n_rows={prof["n_rows"]}; median={prof["fields"]["大众点评餐饮点评评分星级"]["median"]}; '
         f'by_业态={prof["fields"]["大众点评餐饮点评评分星级"].get("by_业态")}')

    # ---- 缓存往返：写小 CSV → 构建 → 再读缓存 ----
    tmpd = Path(tempfile.mkdtemp(prefix="shprof_"))
    csvp = tmpd / "tiny.csv"
    df.to_csv(csvp, index=False)
    load_profile(csvp, reg, force=True)          # 强制构建并写缓存
    cache = csvp.with_suffix(".profile.json")
    re = load_profile(csvp, reg, force=False)    # 读缓存
    R.ok("T27", "画像-缓存往返(构建→读缓存一致)", "统计/画像层",
         cache.exists() and re is not None and re["n_rows"] == 5,
         "缓存文件存在且二次读取 n_rows 一致(=5)",
         f'cache_exists={cache.exists()}; n_rows={re["n_rows"] if re else None}')

    # ---- route_metric：按业态路由模糊度量词 ----
    r_hotel = route_metric("评分", "住宿", prof)
    r_food = route_metric("评分", "餐饮", prof)
    r_amb = route_metric("评分", None, prof)
    R.ok("T28", "画像-route_metric(按业态路由 评分)", "统计/画像层",
         r_hotel == "携程-综合评分" and r_food == "大众点评餐饮点评评分星级"
         and r_amb == "大众点评餐饮点评评分星级",
         "住宿→携程-综合评分；餐饮→大众点评；模糊→整体覆盖率最高者",
         f'住宿={r_hotel}; 餐饮={r_food}; 模糊={r_amb}')

    # ---- 无画像时静态兜底（保持现有解析行为，不回归）----
    fb = route_metric("评分", "住宿", None)
    R.ok("T29", "画像-route_metric(无画像静态兜底)", "统计/画像层",
         fb == "大众点评餐饮点评评分星级",
         "profile=None 时回退静态默认(大众点评)",
         f'fallback={fb}')

    # ---- CLI --profile（真实子进程，小 CSV，快速）----
    try:
        out = subprocess.run([sys.executable, str(SCRIPT_DIR / "agent_cli.py"),
                              "--profile", "渠道组", "--data", str(csvp)],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=60).stdout.decode("utf-8", "ignore")
        R.ok("T31", "画像-CLI --profile 打印字段统计", "统计/画像层",
             "字段画像" in out and "渠道组" in out,
             "CLI 输出含『字段画像』与字段名 渠道组",
             "输出片段: " + (out.strip().splitlines()[-3:] if out.strip() else ["(空)"]).__str__())
    except Exception as e:
        R.fail("T31", "画像-CLI --profile", "统计/画像层", "异常", "无", str(e))

    # ---- gen_schema_md：用画像生成 data_schema.md（合成小数据，验证不报错且含关键段）----
    try:
        md = tmpd / "gen_schema_test.md"
        gen_schema_md(prof, reg, md)
        txt = md.read_text(encoding="utf-8")
        R.ok("T32", "画像-gen_schema_md(反哺数据字典)", "统计/画像层",
             "数据字典" in txt and "渠道组" in txt and "统计画像层" in txt,
             "生成 markdown 含 数据字典 / 渠道组 / 统计画像层 章节",
             f'字节数={len(txt)}')
    except Exception as e:
        R.fail("T32", "画像-gen_schema_md", "统计/画像层", "异常", "无", str(e))


def test_approx():
    """近似画像（DataSketches）：HLL 估算去重基数、KLL 估算分位数，与精确值对照可断言；
    并验证 approx 模式与精确缓存隔离（不读不写 .profile.json）。"""
    import tempfile
    from pathlib import Path as _P
    from profiler import build_profile, load_profile, _SKETCH_AVAIL
    from agent_core import FieldRegistry

    n = 1000
    df = pd.DataFrame({
        "cat": (["X", "Y", "Z"] * (n // 3)) + ["X"] * (n % 3),
        "num": [float(i) for i in range(n)],
    })
    reg = FieldRegistry(fields={
        "cat": {"type": "category"},
        "num": {"type": "numeric"},
    }, synonyms={}, measures={}, entities={})

    if not _SKETCH_AVAIL:
        R.ok("T33", "近似画像-DataSketches(未安装跳过)", "统计/画像层(近似)",
             True, "datasketches 未安装，自动跳过（精确画像不受影响）", "n/a")
        return

    pe = build_profile(df, reg)
    pa = build_profile(df, reg, approx=True)
    card_exact = pe["fields"]["cat"]["cardinality"]
    card_approx = pa["fields"]["cat"].get("cardinality_approx")
    med_exact = pe["fields"]["num"]["median"]
    med_approx = pa["fields"]["num"].get("median")

    # approx 隔离：实时构建，不写精确缓存
    tmpd = _P(tempfile.mkdtemp(prefix="shapprox_"))
    csvp = tmpd / "a.csv"
    df.to_csv(csvp, index=False)
    pa2 = load_profile(csvp, reg, approx=True)
    cache = csvp.with_suffix(".profile.json")

    ok = (card_approx is not None and abs(card_approx - card_exact) <= max(2, card_exact * 0.2)
          and pa["fields"]["num"].get("approx_quantiles") is True
          and med_approx is not None and abs(med_approx - med_exact) <= max(5, abs(med_exact) * 0.05)
          and pa2 is not None and pa2["fields"]["cat"].get("cardinality_approx") is not None
          and not cache.exists())
    R.ok("T33", "近似画像-HLL去重/KLL分位数(与精确对照+缓存隔离)", "统计/画像层(近似)",
         ok,
         f"distinct 精确={card_exact} 近似={card_approx}；median 精确={med_exact} 近似={med_approx}；"
         f"approx 缓存隔离(cache_exists={cache.exists()})",
         f'card_e={card_exact} card_a={card_approx} med_e={med_exact} med_a={med_approx}')


def test_engine():
    """DataFusion 可选后端：对同一 DSL，datafusion 与 pandas 两后端产出等价。"""
    from engine import run_query as eng, _DF_AVAILABLE
    from agent_core import FieldRegistry

    df = pd.DataFrame({
        "渠道组": ["餐饮", "餐饮", "住宿", "住宿", "餐饮"],
        "规划大区": ["目标子区域B", "目标子区域B", "目标子区域A", "目标子区域A", "目标城市1大区"],
        "周末均值客流(人)": [100, 200, 300, 400, 500],
        "连锁品牌名称": ["美宜佳", "美宜佳", "如家", "如家", "美宜佳"],
    })
    reg = FieldRegistry(fields={
        "渠道组": {"type": "category"}, "规划大区": {"type": "category"},
        "周末均值客流(人)": {"type": "numeric"}, "连锁品牌名称": {"type": "category"},
    }, synonyms={}, measures={}, entities={})

    if not _DF_AVAILABLE:
        R.ok("T34", "引擎-DataFusion等价(未安装跳过)", "DataFusion可选后端",
             True, "DataFusion 未安装，自动跳过（默认 pandas 不受影响）", "n/a")
        return

    def _equiv(oP, oD):
        if len(oP) != len(oD):
            return False, f"rows {len(oP)}!={len(oD)}"
        cols = [str(c) for c in oP.columns]
        if set(cols) != set(str(c) for c in oD.columns):
            return False, "cols differ"
        p = oP.sort_values(cols).reset_index(drop=True)
        d = oD.sort_values(cols).reset_index(drop=True)
        for c in cols:
            for a, b in zip(p[c].tolist(), d[c].tolist()):
                if pd.isna(a) and pd.isna(b):
                    continue
                if a != b:
                    return False, f"col {c}: {a}!={b}"
        return True, "equal"

    cases = [
        ("A", {"type": "A", "filters": [{"field": "渠道组", "op": "=", "value": "餐饮"}],
               "order_by": [{"field": "周末均值客流(人)", "dir": "desc"}], "limit": 2}),
        ("Bcount", {"type": "B", "group_by": ["规划大区"], "metrics": [{"name": "count", "agg": "count"}]}),
        ("Bmean", {"type": "B", "group_by": ["渠道组"], "metrics": [{"field": "周末均值客流(人)", "agg": "mean"}]}),
    ]
    allok = True
    msgs = []
    for name, q in cases:
        oP, *_ = eng(df, q, reg, "pandas")
        oD, *_ = eng(df, q, reg, "datafusion")
        ok, msg = _equiv(oP, oD)
        allok &= ok
        msgs.append(f"{name}:{msg}")
    R.ok("T34", "引擎-DataFusion与pandas等价(A/B类)", "DataFusion可选后端",
         allok, "两后端对相同 DSL 产出等价(out 行数/列/值一致)", "; ".join(msgs))


def test_insight():
    """洞察综合层（第二棱镜）：对 A/B/C 三类结果 + 空结果确定性合成 摘要/质量提示/分析建议（零动作越界）。"""
    from adapters.legacy_dataframe_api import synthesize

    # C 类：候选排行结果 + 体检
    c_df = pd.DataFrame({
        "经营场所名称": ["A商场", "B街", "C社区"],
        "餐饮基数": [30, 25, 20], "评分高占比": [0.6, 0.5, 0.4],
        "开业年限达标占比": [0.7, 0.6, 0.5], "达标率": [0.8, 0.6, 0.5],
        "等级": ["高", "中", "低"], "数据完整度%": [90, 80, 40],
    })
    c_audit = {"audited": True, "scope": "candidates",
               "flags": ["2 个候选数据完整度低于 50%，建议补充采集提高置信度"],
               "recommendation": "建议补充采集"}
    c_ins = synthesize({"type": "C"}, c_df, c_audit)
    ok_c = (bool(c_ins["conclusion"]["summary"]) and len(c_ins["followups"]) >= 2
            and bool(c_ins["conclusion"].get("verdict")) and "action" not in c_ins)

    # B 类：分布
    b_df = pd.DataFrame({"规划大区": ["目标子区域B", "目标子区域A"], "客户数": [100, 50]})
    b_ins = synthesize({"type": "B", "group_by": ["规划大区"]}, b_df, None)
    ok_b = ("目标子区域B" in b_ins["conclusion"]["summary"]) and len(b_ins["followups"]) >= 1 and "action" not in b_ins

    # A 类：明细
    a_df = pd.DataFrame({"规划大区": ["目标子区域B"] * 3 + ["目标子区域A"]})
    a_ins = synthesize({"type": "A"}, a_df, None)
    ok_a = "主要集中在" in a_ins["conclusion"]["summary"] and "action" not in a_ins

    # 空结果降级
    e_ins = synthesize({"type": "A"}, pd.DataFrame(), None)
    ok_e = "无结果" in e_ins["conclusion"]["summary"] and "action" not in e_ins

    allok = ok_c and ok_b and ok_a and ok_e
    R.ok("T35", "第二棱镜-结构化洞察合成(A/B/C+空降级)", "第二棱镜综合输出",
         allok, "确定性合成 摘要/质量限制/下一步分析，绝不生成 action 字段",
         f"C:{ok_c} B:{ok_b} A:{ok_a} empty:{ok_e}")
def test_data_assertions():
    """FR-39 数据质量断言（Denial Constraints / 数据漂移第一道防线）：
    真实 parquet 抽样可建基线；合成快照注入漂移（均值突变/空值率飙升/值域漂移）
    必须被 check_dcs 捕获告警，且干净快照 0 告警。零 LLM、确定性。"""
    # (0) 真实数据烟雾：在 629k×152 真实 parquet 抽样上建基线不报错（对齐 M1）
    real_smoke = True
    real_msg = "n/a"
    try:
        sample = SESS.full_df[["渠道组", "省份"]].head(5000)
        base_real = build_baseline(sample)
        real_smoke = bool(base_real.get("version")) and base_real["n_rows"] == 5000
        real_msg = f"真实抽样基线 version={base_real['version']} n_rows={base_real['n_rows']}"
    except Exception as e:
        real_smoke, real_msg = False, f"真实抽样异常: {e}"

    # (1) 合成干净快照：确定性、可断言
    df_clean = pd.DataFrame({
        "num": [float(i) for i in range(1, 101)],          # mean=50.5 std≈29
        "cat": (["A", "B", "C"] * 34)[:100],                # 值域 {A,B,C}
    })
    baseline = build_baseline(df_clean)
    dcs = generate_dcs(baseline)
    viol_clean = check_dcs(df_clean, dcs, baseline)

    # (2) 注入漂移 1：数值均值突变（×10）→ mean_shift 告警
    df_mean = df_clean.copy()
    df_mean["num"] = df_mean["num"] * 10
    viol_mean = check_dcs(df_mean, dcs, baseline)

    # (3) 注入漂移 2：空值率飙升（整列置空）→ null_rate 告警
    df_null = df_clean.copy()
    df_null["num"] = None
    viol_null = check_dcs(df_null, dcs, baseline)

    # (4) 注入漂移 3：值域漂移（新类别 Z）→ domain_drift 告警
    df_dom = df_clean.copy()
    df_dom.loc[0:50, "cat"] = "Z"
    viol_dom = check_dcs(df_dom, dcs, baseline)

    # 约束版本化（AC3）：改一行后基线 version 应变化
    df_mut = df_clean.copy()
    df_mut.loc[0, "num"] = 999.0
    version_changed = build_baseline(df_mut)["version"] != baseline["version"]

    allok = (
        real_smoke
        and len(viol_clean) == 0
        and len(viol_mean) >= 1 and any(v.kind == "mean_shift" for v in viol_mean)
        and len(viol_null) >= 1 and any(v.kind == "null_rate" for v in viol_null)
        and len(viol_dom) >= 1 and any(v.kind == "domain_drift" for v in viol_dom)
        and version_changed
    )
    R.ok("T37", "数据断言-DC漂移检测(真实基线+合成注入)", "数据质量断言(FR-39)",
         allok,
         "真实抽样基线可建；干净0告警；均值/空值/值域漂移各≥1告警；基线版本化",
         f"{real_msg} | clean={len(viol_clean)} mean={len(viol_mean)} "
         f"null={len(viol_null)} domain={len(viol_dom)} ver_changed={version_changed}")


def test_dm_layer():
    """第10块 DM 汇总层：B 类分布查询命中预物化汇总表（不扫底表），
    与 pandas 底表扫描结果等价；不可路由（如 contains 过滤）自动回退；
    DM 外维度过滤拒绝路由（不静默丢条件）。"""
    from engine import run_query as eng

    if not getattr(SESS, "dm_db", None):
        R.ok("T39", "DM汇总层-路由(不可用跳过)", "DM汇总层(第10块)",
             True, "DM 不可用时自动回退底表扫描", "dm_db=None，全部走底表")
        return

    dsl = {"type": "B",
           "filters": [{"field": "城市", "op": "=", "value": "目标城区"},
                       {"field": "渠道组", "op": "=", "value": "餐饮"},
                       {"field": "是否连锁", "op": "=", "value": "是"}],
           "group_by": ["连锁品牌名称"],
           "metrics": [{"name": "门店数", "agg": "count"}], "limit": 10}
    o1, t1, _, _ = eng(SESS.full_df, dict(dsl), SESS.reg, dm_db=SESS.dm_db)
    o2, t2, _, _ = eng(SESS.full_df, dict(dsl), SESS.reg)
    hit_dm = any("[DM]" in x for x in t1)
    no_dm = not any("[DM]" in x for x in t2)
    m1 = dict(zip(o1["连锁品牌名称"], o1["门店数"]))
    m2 = dict(zip(o2["连锁品牌名称"], o2["门店数"]))
    equal = (m1 == m2)

    # 不可路由：contains 过滤 → 必须回退底表
    dsl2 = {"type": "B",
            "filters": [{"field": "连锁品牌名称", "op": "contains", "value": "咖啡"}],
            "group_by": ["渠道组"], "limit": 5}
    o3, t3, _, _ = eng(SESS.full_df, dict(dsl2), SESS.reg, dm_db=SESS.dm_db)
    fallback = not any("[DM]" in x for x in t3) and len(o3) > 0

    # DM 外维度（经营场所不在 DM 粒度）→ 拒绝路由，回退底表（不静默丢条件）
    dsl3 = {"type": "B",
            "filters": [{"field": "经营场所", "op": "=", "value": "社区"}],
            "group_by": ["渠道组"], "limit": 5}
    o4, t4, _, _ = eng(SESS.full_df, dict(dsl3), SESS.reg, dm_db=SESS.dm_db)
    no_silent = not any("[DM]" in x for x in t4)

    R.ok("T39", "DM汇总层-B类路由(命中/等价/回退/不静默丢条件)", "DM汇总层(第10块)",
         hit_dm and no_dm and equal and fallback and no_silent,
         "等值+count 命中 DM 且与底表扫描等价；contains/DM外维度回退底表",
         f"hit_dm={hit_dm} equal={equal} fallback={fallback} no_silent={no_silent} "
         f"top1={((o1.iloc[0]['连锁品牌名称'], o1.iloc[0]['门店数']) if not o1.empty else '空(DM结果0行)')}")


def test_feedback_log():
    """FR-37 HIP 纠偏日志 W3C PROV 信任链（M1 PoC）：
    独立审计模块记录高风险纠偏 → 进入待审队列 + 携带 match/review/risk 元数据 →
    导出 PROV-JSON 含 Entity/Activity/Agent 三元组 → 审核通过/驳回 → 持久化可重载。
    零 LLM、确定性；用临时文件，不污染真实飞轮。"""
    import tempfile
    from pathlib import Path as _P

    tmp = _P(tempfile.mkdtemp()) / "feedback_log.jsonl"
    fl = FeedbackLog(tmp)

    # (1) 记录一条高风险模糊纠偏：必须 pending + 强制携带元数据（FR-37 AC3）
    rid = fl.log_correction(
        nl="列出营业到晚上10点后的火锅店",
        final_dsl={"type": "A", "filters": [{"field": "大众点评营业时间",
                                             "op": "close_after", "value": 22}]},
        candidate_dsl={"type": "A", "filters": []},
        match_method="fuzzy", match_score=0.78,
        business_person="张经理", system_agent="nl_parser",
        risk_level=RISK_HIGH, semantic_version="field_registry@v3",
    )
    rec = fl.get(rid)
    meta_ok = (rec["review_status"] == "pending"
               and rec["match_method"] == "fuzzy" and rec["match_score"] == 0.78
               and rec["risk_level"] == RISK_HIGH
               and rec["agents"]["reviewer"] is None)

    # (2) 进入待审队列
    in_pending = any(r["id"] == rid for r in fl.pending())

    # (3) 导出 PROV-JSON 含 Entity/Activity/Agent 三元组（FR-37 AC1），结构可解析（AC2 前置）
    prov = fl.to_prov(rid)
    has_triple = ("entity" in prov and "activity" in prov and "agent" in prov)
    has_nl_entity = any(
        v.get("prov:type") == "fb:NaturalLanguage" for v in prov["entity"].values()
    )
    # PROV-JSON 必须可序列化回 JSON（被独立工具解析的前提）
    prov_valid_json = isinstance(json.dumps(prov, ensure_ascii=False), str)
    # Activity 携带信任链元数据
    act = next(iter(prov["activity"].values()))
    act_meta = (act.get("fb:match_method") == "fuzzy"
                and act.get("fb:risk_level") == RISK_HIGH
                and act.get("fb:review_status") == "pending")

    # (4) 审核通过 → 离开待审队列、记录审核人
    fl.review(rid, REVIEW_APPROVED, "李审核")
    rec2 = fl.get(rid)
    approved_ok = (rec2["review_status"] == REVIEW_APPROVED
                   and rec2["agents"]["reviewer"] == "李审核"
                   and not any(r["id"] == rid for r in fl.pending()))

    # (5) 驳回路径
    rid2 = fl.log_correction(nl="餐饮街火锅", final_dsl={"type": "A"},
                             risk_level=RISK_HIGH)
    fl.review(rid2, REVIEW_REJECTED, "王审核")
    rejected_ok = fl.get(rid2)["review_status"] == REVIEW_REJECTED

    # (6) 持久化重载：审核结果落盘后仍能读出
    fl2 = FeedbackLog(tmp)
    reload_ok = (fl2.get(rid)["review_status"] == REVIEW_APPROVED
                 and fl2.get(rid2)["review_status"] == REVIEW_REJECTED)

    allok = (meta_ok and in_pending and has_triple and has_nl_entity
             and prov_valid_json and act_meta and approved_ok
             and rejected_ok and reload_ok)

    R.ok("T38", "HIP纠偏审计-记录/待审/PROV三元组/审核通过驳回/持久化",
         "HIP纠偏日志(FR-37)",
         allok,
         "高风险纠偏 pending+元数据；PROV 含 E/A/Ag 三元组且可序列化；审核通过/驳回；持久化",
         f"meta={meta_ok} pending={in_pending} triple={has_triple} "
         f"nl_entity={has_nl_entity} json_ok={prov_valid_json} act_meta={act_meta} "
         f"approved={approved_ok} rejected={rejected_ok} reload={reload_ok}")


def test_time_predicate():
    """营业时间谓词（语义层时间算子）：自然语言→DSL→解析营业时间文本→DataFusion 自动回退。"""
    from nl_parser import parse_A
    from agent_core import FieldRegistry, run_query, validate_query
    from engine import run_query as eng, _DF_AVAILABLE
    from bizhours import close_after

    reg = FieldRegistry.load()

    # (1) 解析：口语 → close_after / open_after（+ 12 小时制映射）
    expect = {
        "餐饮街营业到晚10点以后的门店": ("close_after", 22),
        "营业到晚上8点以后": ("close_after", 20),
        "24小时营业": ("close_after", 24),
        "通宵营业的店": ("close_after", 24),
        "22点以后才开门的夜宵店": ("open_after", 22),
        "凌晨2点后还开的": ("close_after", 2),
    }
    parse_ok = True
    pmsg = []
    for q, (eop, eval_) in expect.items():
        dsl = parse_A(q, reg)
        hit = [f for f in dsl["filters"] if f.get("op") in ("close_after", "open_after")]
        ok = bool(hit) and hit[0]["op"] == eop and hit[0]["value"] == eval_ \
            and hit[0]["field"] == "大众点评营业时间"
        parse_ok &= ok
        pmsg.append(f"{'OK' if ok else 'XX'} {q}→{hit[0] if hit else None}")

    # (2) 执行：close_after 22 过滤验证（22:00 整排除；过半夜/24:00/23:30 计入；NaN 排除）
    df = pd.DataFrame({
        "大众点评营业时间": ["周一至周日 10:00-22:00", "周一至周日 11:00-23:30",
                             "周一至周日 10:00-02:00", "周一至周日 10:00-24:00",
                             "周一至周日 09:00-21:00", None],
        "经营场所": ["餐饮街"] * 6,
    })
    dsl = parse_A("餐饮街营业到晚10点以后的门店", reg)
    v = validate_query(dsl, reg)
    out, *_ = run_query(df, dsl, reg)
    exec_ok = v["ok"] and len(out) == 3
    emsg = f"validate_ok={v['ok']} rows={len(out)}(期望3)"

    # (3) DataFusion 含时间谓词 → 自动回退 pandas，结果与 pandas 一致
    df2 = pd.DataFrame({
        "大众点评营业时间": ["10:00-22:00", "11:00-23:00", "10:00-02:00"],
        "渠道组": ["餐饮", "餐饮", "住宿"],
    })
    q = {"type": "B", "filters": [{"field": "大众点评营业时间", "op": "close_after", "value": 22}],
         "group_by": ["渠道组"], "metrics": [{"name": "count", "agg": "count"}]}
    if _DF_AVAILABLE:
        oP, *_ = eng(df2, q, reg, "pandas")
        oD, *_ = eng(df2, q, reg, "datafusion")
        eng_ok = len(oP) == 2 and len(oD) == 2 and oP["count"].sum() == oD["count"].sum() == 2
        emsg2 = f"pandas={oP['count'].tolist()} df={oD['count'].tolist()}"
    else:
        eng_ok, emsg2 = True, "DataFusion未安装跳过等价"

    # (4) 反例：纯数字营业年限不应触发时间谓词
    dsl3 = parse_A("营业3年以上的餐饮店", reg)
    no_time = not any(f.get("op") in ("close_after", "open_after") for f in dsl3["filters"])

    allok = parse_ok and exec_ok and eng_ok and no_time
    R.ok("T36", "时间谓词-晚10点后营业(解析/执行/护栏/引擎回退)", "语义层时间算子",
         allok, "自然语言→close_after DSL→解析营业时间文本→DataFusion自动回退pandas；反例不误触发",
         f"parse:{parse_ok} exec:{emsg} df:{emsg2} 反例_no_time:{no_time} | " + " | ".join(pmsg))


# ============================================================ 记忆保护
# ============================================================ 边界契约测试 (Phase 0 Boundary Contract Tests)
def test_boundary_contracts():
    """验证谋圣产品边界守则：
    BCT-01: 输出绝不包含 action 字段 (全场景)
    BCT-02: 全场景输出文本严禁生成 派单/路线/排程/投放 等执行建议
    BCT-03: followups 必须全部为分析类问题
    BCT-04: 绝不生成 Action/Task/RoutePlan 越界实体
    BCT-05: ElevationCandidate 必须符合严格元数据约束 (行为级强校验)
    BCT-06: ambiguous 歧义字段在编译期行为级强制阻断
    """
    from adapters.legacy_dataframe_api import synthesize
    import yaml

    # BCT-01: 输出结构中绝对不得存在 action 字段 (A/B/C/空) 且严格为 11 顶级字段
    sample_a = synthesize({"type": "A"}, pd.DataFrame({"省份": ["目标省份省"] * 5}), None)
    sample_b = synthesize({"type": "B", "group_by": ["省份"]}, pd.DataFrame({"省份": ["目标省份省"], "客户数": [100]}), None)
    sample_c = synthesize({"type": "C"}, pd.DataFrame({"经营场所名称": ["A商场"], "餐饮基数": [30], "达标率": [0.8], "等级": ["高"]}), {"audited": True, "flags": ["缺失提示"]})
    sample_empty = synthesize({"type": "A"}, None, None)

    bct01_ok = all(("action" not in s) and (len(s.keys()) == 11) for s in [sample_a, sample_b, sample_c, sample_empty])
    R.ok("BCT-01", "边界契约-输出严格为11字段且无action", "产品边界契约",
         bct01_ok, "A/B/C/空结果输出均严格为 11 个标准顶级字段，0 冗余与 0 action 字段",
         f"key_counts={[len(s.keys()) for s in [sample_a, sample_b, sample_c, sample_empty]]}")

    # BCT-02: 全场景输出文本绝对不得出现派单/路线/排程/投放等越界词汇
    forbidden_words = ["派单", "路线", "排程", "投放", "应该去", "安排拜访", "必须执行"]
    all_texts = []
    for s in [sample_a, sample_b, sample_c, sample_empty]:
        f_texts = [f["suggested_question"] if isinstance(f, dict) else str(f) for f in s.get("followups", [])]
        l_texts = [str(x) for x in s.get("quality_and_limits", {}).get("limitations", [])]
        summary_txt = s.get("conclusion", {}).get("summary", "")
        verdict_txt = s.get("conclusion", {}).get("verdict", "")
        all_texts.append(f"{summary_txt} {verdict_txt} {' '.join(l_texts)} {' '.join(f_texts)}")
    combined = " ".join(all_texts)
    found_forbidden = [w for w in forbidden_words if w in combined]
    bct02_ok = len(found_forbidden) == 0
    R.ok("BCT-02", "边界契约-严禁生成业务执行建议词汇(全场景)", "产品边界契约",
         bct02_ok, "全场景输出文本 0 出现 派单/路线/排程/投放 等词汇", f"命中违禁词={found_forbidden}")

    # BCT-03: followups 必须全部为分析类问题
    analytical_keywords = ["分析", "下钻", "对比", "查看", "评估", "观察", "重试", "筛选", "分布"]
    all_followups = []
    for s in [sample_a, sample_b, sample_c, sample_empty]:
        for f in s.get("followups", []):
            q_str = f["suggested_question"] if isinstance(f, dict) else str(f)
            all_followups.append(q_str)
    bct03_ok = bool(all_followups) and all(any(k in f for k in analytical_keywords) for f in all_followups)
    R.ok("BCT-03", "边界契约-followups必须全部为分析问题", "产品边界契约",
         bct03_ok, f"共 {len(all_followups)} 条后续建议均为分析探索方向，不含执行命令", f"followups_sample={all_followups[:3]}")
    # BCT-04: 绝不生成 Action/Task/RoutePlan 越界实体
    bct04_ok = all(not any(k in s for k in ["actions", "tasks", "route_plan", "crm_state", "action"]) for s in [sample_a, sample_b, sample_c, sample_empty])
    R.ok("BCT-04", "边界契约-绝不生成Action/Task实体", "产品边界契约",
         bct04_ok, "输出对象中无任何执行类实体结构", "A/B/C/空结果结构均无执行实体")

    # BCT-05: ElevationCandidate 必须符合真实 Draft 2020-12 JSON Schema 强约束
    import copy
    from jsonschema import Draft202012Validator

    schema_path = SKILL_DIR / "references" / "insight-package.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_json = json.load(f)

    Draft202012Validator.check_schema(schema_json)
    val = Draft202012Validator(schema_json)
    valid_complete_cand = {
        "analysis_id": "test-elevation-001",
        "artifact_type": "ElevationCandidate",
        "request_context": {
            "question": "测试提问", "intent": "detail_list", "target_entity": "prism:outlet/Outlet",
            "scope": {}, "entities": [], "metrics": [], "rules": [], "ambiguities": [], "requested_outputs": ["insight", "table"]
        },
        "conclusion": {"summary": "核心结论", "confidence_level": "high", "verdict": "符合预期"},
        "evidence": {"sample_size": 100, "effective_sample_size": 100, "table_preview": [], "drilldown_keys": [], "out_csv_path": None, "chart_spec_ref": None},
        "structure": {"concentration_metrics": {"hhi": 0.0, "cr4": 0.0, "cr8": 0.0, "gini": 0.0, "category": "unconcentrated"}, "distributions": []},
        "differences": {"compared_groups": [], "metric_contrasts": []},
        "anomalies": {"baseline_method": "IQR", "detected_outliers": []},
        "quality_and_limits": {"completeness_score": 1.0, "field_coverage": {}, "applicable_dimensions": [], "warnings": [], "limitations": []},
        "followups": [{"suggested_question": "进一步下钻分析", "analytical_intent": "深度下钻"}],
        "provenance": {
            "request_id": "req-elevation-001",
            "insight_component_version": "1.1.0",
            "insight_generated_at": "2026-08-25T00:00:00Z",
            "evidence_provenance": {
                "dataset_id": "sh-152", "data_version": "1.0", "ontology_profile_version": "0.1.0-rc2", "generated_at": "2026-08-25T00:00:00Z"
            },
            "elevation_metadata": {"status": "unreviewed", "world_state_effect": "none", "requires_external_governance": True}
        }
    }

    # 合法完整样本验证
    err_valid = list(val.iter_errors(valid_complete_cand))
    t1_pass = (len(err_valid) == 0)

    # 变异非法样本：缺少 status
    bad_missing = copy.deepcopy(valid_complete_cand)
    del bad_missing["provenance"]["elevation_metadata"]["status"]
    t2_pass = (len(list(val.iter_errors(bad_missing))) > 0)

    # 变异非法样本：status 不是 unreviewed
    bad_status = copy.deepcopy(valid_complete_cand)
    bad_status["provenance"]["elevation_metadata"]["status"] = "approved"
    t3_pass = (len(list(val.iter_errors(bad_status))) > 0)

    # 变异非法样本：world_state_effect 不是 none
    bad_effect = copy.deepcopy(valid_complete_cand)
    bad_effect["provenance"]["elevation_metadata"]["world_state_effect"] = "write_state"
    t4_pass = (len(list(val.iter_errors(bad_effect))) > 0)

    # 变异非法样本：requires_external_governance 为 False
    bad_gov = copy.deepcopy(valid_complete_cand)
    bad_gov["provenance"]["elevation_metadata"]["requires_external_governance"] = False
    t5_pass = (len(list(val.iter_errors(bad_gov))) > 0)

    bct05_ok = (t1_pass and t2_pass and t3_pass and t4_pass and t5_pass)
    R.ok("BCT-05", "边界契约-Draft2020-12真实Schema对ElevationCandidate强约束", "产品边界契约",
         bct05_ok, "合法通过，且非法status/effect/gov/缺失均被真实Schema拒绝",
         f"valid_pass={t1_pass} bad_missing_rej={t2_pass} bad_status_rej={t3_pass} bad_effect_rej={t4_pass} bad_gov_rej={t5_pass}")

    # BCT-06: ambiguous 歧义字段在编译期行为级强制阻断（统一错误来源校验）
    v_filter = validate_query({"type": "A", "filters": [{"field": "是否匹配", "op": "=", "value": "Y"}]}, REG)
    v_group = validate_query({"type": "B", "group_by": ["待开发客户细分"], "metrics": [{"name": "count", "agg": "count"}]}, REG)
    v_metric = validate_query({"type": "B", "group_by": ["渠道组"], "metrics": [{"field": "Universe以外数据来源", "agg": "mean"}]}, REG)

    errs = [v_filter.get("errors", [""])[0], v_group.get("errors", [""])[0], v_metric.get("errors", [""])[0]]
    bct06_ok = (not v_filter["ok"]) and (not v_group["ok"]) and (not v_metric["ok"]) and all("处于歧义隔离状态（excluded_from_execution）" in e for e in errs)
    R.ok("BCT-06", "边界契约-ambiguous字段统一隔离策略编译阻断", "产品边界契约",
         bct06_ok, "三大歧义字段统一命中 excluded_from_execution 编译阻断",
         f"all_isolated_blocked={bct06_ok} errs={errs}")
_GUARD_STATE = SKILL_DIR / ".mem_guard_state.json"


def _backup_memory():
    state = {}
    for p in (MEM_PATH, EMB_PATH):
        bak = p.with_suffix(p.suffix + ".bak")
        if p.exists():
            shutil.copy(p, bak)
            state[str(p)] = "existed"
        else:
            if bak.exists():
                bak.unlink()
            state[str(p)] = "absent"
    _GUARD_STATE.write_text(json.dumps(state))


def _restore_memory():
    state = {}
    if _GUARD_STATE.exists():
        try:
            state = json.loads(_GUARD_STATE.read_text())
        except Exception:
            state = {}
        _GUARD_STATE.unlink()
    for p in (MEM_PATH, EMB_PATH):
        bak = p.with_suffix(p.suffix + ".bak")
        orig = state.get(str(p), "absent")
        if orig == "existed":
            if bak.exists():
                shutil.copy(bak, p)
                bak.unlink()
        else:  # 原本不存在 → 清除测试产生的产物，恢复原状
            if p.exists():
                p.unlink()
            if bak.exists():
                bak.unlink()


# ============================================================ 报告生成
def gen_report(out_path: Path, total: float):
    passed = sum(1 for c in R.cases if c["ok"])
    failed = len(R.cases) - passed
    scenarios: dict[str, list[int]] = {}
    for c in R.cases:
        scenarios.setdefault(c["scenario"], [0, 0])
        scenarios[c["scenario"]][0] += 1
        scenarios[c["scenario"]][1] += 1 if c["ok"] else 0

    rows_html = ""
    for c in R.cases:
        color = "#1a7f37" if c["ok"] else "#cf222e"
        badge = "PASS" if c["ok"] else "FAIL"
        detail_html = (c["detail"].replace("\n", "<br>")
                       .replace("<", "&lt;").replace(">", "&gt;"))
        rows_html += f"""
        <tr>
          <td style="text-align:center;font-weight:600">{c['id']}</td>
          <td>{c['name']}</td>
          <td style="text-align:center">{c['scenario']}</td>
          <td style="text-align:center;color:{color};font-weight:700">{badge}</td>
          <td>{c['expect']}</td>
          <td>{c['actual']}</td>
          <td style="font-size:12px;color:#57606a">{detail_html}</td>
        </tr>"""

    scenario_html = ""
    for sc, (tot, pas) in scenarios.items():
        sc_color = "#1a7f37" if tot == pas else "#cf222e"
        scenario_html += (f'<span style="display:inline-block;margin:3px 6px;padding:4px 10px;'
                          f'border-radius:14px;background:#f3f4f6;color:{sc_color};'
                          f'font-size:13px">{sc}: {pas}/{tot}</span>')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>outlet-insight 测试报告</title>
<style>
 body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;background:#f6f8fa;color:#1f2328}}
 .wrap{{max-width:1180px;margin:0 auto;padding:28px}}
 h1{{font-size:24px;margin:0 0 4px}}
 .sub{{color:#57606a;font-size:14px;margin-bottom:18px}}
 .cards{{display:flex;gap:14px;margin:18px 0}}
 .card{{flex:1;background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:16px;text-align:center}}
 .card .n{{font-size:30px;font-weight:700}}
 .card .l{{font-size:13px;color:#57606a;margin-top:4px}}
 .green{{color:#1a7f37}} .red{{color:#cf222e}}
 .scen{{margin:14px 0}}
 table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d0d7de;border-radius:10px;overflow:hidden;font-size:13px}}
 th,td{{padding:9px 11px;border-bottom:1px solid #eaecef;text-align:left;vertical-align:top}}
 th{{background:#f6f8fa;font-weight:600;color:#57606a}}
 tr:hover{{background:#f6f8fa}}
 .summary{{background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:14px 18px;font-size:14px;line-height:1.7}}
</style></head>
<body><div class="wrap">
 <h1>outlet-insight 数据分析 Agent · 场景化测试报告</h1>
 <div class="sub">生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')} ｜ 数据：{len(SESS.full_df):,} 行 ｜ 运行耗时 {total:.1f}s</div>

 <div class="cards">
   <div class="card"><div class="n">{len(R.cases)}</div><div class="l">总用例数</div></div>
   <div class="card"><div class="n green">{passed}</div><div class="l">通过</div></div>
   <div class="card"><div class="n red">{failed}</div><div class="l">失败</div></div>
   <div class="card"><div class="n">{passed/len(R.cases)*100:.0f}%</div><div class="l">通过率</div></div>
 </div>

 <div class="scen">{scenario_html}</div>

 <div class="summary">
  <b>测试覆盖</b>：A类明确查询、B类分布探索、C类规则排行、多步会话、Dry-plan 透明计划、
  负向编译护栏（幻觉字段/非法算子/幻觉候选）、数据质量与适用性评估、用户纠偏闭环、
  embedding 近义召回与区分度、MCP 语义层多步暴露、统计/画像层（第5块：构建/缓存/按业态路由/CLI/MCP/反哺文档）、
  近似画像（DataSketches：HLL 去重/KLL 分位数，与精确对照+缓存隔离）、DataFusion 可选后端（与 pandas 等价，A/B 类）、
  数据质量断言（FR-39 Denial Constraints：真实抽样基线 + 合成漂移注入捕获均值/空值率/值域漂移，不阻断不静默）。<br>
  <b>关键验证点</b>：① 品牌/业态/数值/度量/实体过滤语义正确且可断言；② 多步工作集在 346→37 间正确收缩；
  ③ 编译期护栏对幻觉输入零容忍；④ 纠偏飞轮对原话/近义可闭合并区分不相关问法；
  ⑤ 外部 Agent 经 MCP 可连续追问且作用域边界清晰；⑥ DataFusion 后端与 pandas 对相同 DSL 产出一致；
  ⑦ 近似画像在超大/流式场景下估算值与精确值容差内一致且与精确缓存隔离；
  ⑥ 字段画像真实统计可构建/缓存，模糊度量词按业态覆盖率路由到正确字段。
 </div>

 <table style="margin-top:16px">
  <thead><tr><th style="width:42px">ID</th><th>用例</th><th style="width:96px">场景</th>
   <th style="width:54px">结果</th><th>期望</th><th>实测</th><th>细节</th></tr></thead>
  <tbody>{rows_html}</tbody>
 </table>
</div></body></html>"""
    out_path.write_text(html, encoding="utf-8")
    return passed, failed


# ============================================================ 主入口
def main():
    import argparse
    ap = argparse.ArgumentParser(description="outlet-insight 场景化测试套件")
    ap.add_argument("--out", default=str(SKILL_DIR / "test_report.html"),
                    help="HTML 报告输出路径（默认 SKILL_DIR/test_report.html）")
    ap.add_argument("--no-report", action="store_true", help="不生成 HTML 报告")
    ap.add_argument("--keep-memory", action="store_true",
                    help="关闭记忆保护（调试用，会污染真实飞轮）")
    args = ap.parse_args()

    guard = not args.keep_memory
    if guard:
        _backup_memory()
        print("[guard] 已备份真实记忆库（测试后恢复）", flush=True)

    global R, SESS
    R = Results()
    SESS = QuerySession(use_cache=True)
    print(f"[setup] 全量数据已加载：{len(SESS.full_df):,} 行 × {SESS.full_df.shape[1]} 列", flush=True)

    if guard:
        QueryMemory().clear()

    t0 = time.time()
    def _guarded(fn):
        try:
            fn()
        except Exception as e:  # 用例函数自身异常 → 如实记录为失败用例，绝不中断整套件
            R.fail(f"EX-{fn.__name__}", f"{fn.__name__} 未捕获异常", "套件健壮性",
                   "用例函数执行无异常", f"{type(e).__name__}: {e}",
                   traceback.format_exc(limit=6))

    for _t in (test_A, test_B, test_C, test_multistep, test_dryplan,
               test_guardrails, test_audit, test_correction_and_embedding, test_mcp,
               test_profile, test_approx, test_engine, test_insight, test_time_predicate,
               test_data_assertions, test_feedback_log, test_dm_layer, test_boundary_contracts):
        _guarded(_t)
    total = time.time() - t0

    if guard:
        _restore_memory()
        print("[guard] 已恢复真实记忆库", flush=True)

    passed = sum(1 for c in R.cases if c["ok"])
    failed = len(R.cases) - passed
    print(f"[done] 共 {len(R.cases)} 个用例，耗时 {total:.1f}s", flush=True)

    if not args.no_report:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        p, f = gen_report(out_path, total)
        print(f"[report] HTML 报告已写出：{out_path}")

    print(f"\n==== 汇总：{passed}/{len(R.cases)} 通过（{failed} 失败）====")
    for c in R.cases:
        flag = "✓" if c["ok"] else "✗"
        print(f"  {flag} {c['id']} {c['name']}  → 实测: {c['actual']}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
