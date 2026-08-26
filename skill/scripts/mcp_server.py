"""
mcp_server.py
=============
语义层 MCP Server（借鉴 ktx 的 agent-native 上下文层思路）。

把本 Skill 的语义层（field_registry）与查询能力通过 MCP 暴露，让别的 AI Agent
也能以「受管指标/维度 + 确定性编译护栏」的方式调用这份数据上下文，
而不是让对方 Agent 直接面对混乱的物理表。

传输：MCP stdio（JSON-RPC 2.0 + Content-Length 帧）。零第三方依赖。
启动即加载一次全量数据，常驻内存。

暴露的 tools：
  - semantic_layer  返回语义层概览（字段数 / 业务度量 / 私有实体）
  - explain         给定自然语言，返回 Dry-plan 透明执行计划（不执行）
  - query           给定自然语言，确定性执行并返回结果摘要 + CSV；
                   支持 session_id + scope，让外部 Agent 也能连续追问
                   （scope=current 在上一步工作集上再算）
  - validate        给定自然语言，返回语义层编译校验结果
  - session_reset   清空指定会话的工作集与步骤历史
  - stat            统计级别查询：命中 DM 预物化汇总表（毫秒级，不扫底表），
                   返回分布/排行 + 每行的下钻过滤键
  - drilldown       给定下钻过滤键，回底表取原始门店明细（常驻内存，不读盘）
  - chart           图形化：stat 级查询 → Flint 渲染成离线 HTML 图表（按度量语义自动配色）

注册到 ~/.workbuddy/mcp.json 示例：
  {
    "mcpServers": {
      "outlet-insight": {
        "command": "/Users/ghb/.workbuddy/binaries/python/envs/store/bin/python",
        "args": ["/Users/ghb/.workbuddy/skills/outlet-insight/scripts/mcp_server.py"]
      }
    }
  }
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from session import QuerySession, _STAY_WORDS, _RESET_WORDS
from nl_parser import explain_query, auto_parse
from agent_core import run_query, validate_query
from profiler import load_profile, set_profile  # 第5块：统计/画像层
from dm_layer import query_stat, query_stat_cross, drilldown_df, DIMENSIONS as DM_DIMENSIONS  # 第10块：DM汇总层
from viz_adapter import build_flint_spec, write_spec  # 第10块：Flint 图形化适配层
from adapters.legacy_dataframe_api import synthesize
# 图形化渲染所需的环境（Node 桥 + 受管 node workspace）
HERE = Path(__file__).resolve().parent
NODE = "/Users/ghb/.workbuddy/binaries/node/versions/22.22.2/bin/node"
NODE_PATH = "/Users/ghb/.workbuddy/binaries/node/workspace/node_modules"
CHART_OUT = Path("/Users/ghb/outlet-insight/output/charts")

# 启动时加载一次全量数据，常驻（MCP 服务进程级单例）
print("[mcp] 启动：加载全量售点数据...", file=sys.stderr)
SESS = QuerySession(use_cache=True)
print(f"[mcp] 就绪：{len(SESS.full_df):,} 行 × {SESS.full_df.shape[1]} 列", file=sys.stderr)
# 第5块：加载字段统计画像（首次构建约 15-30s，之后读缓存秒级），供 profile tool 与解析路由
PROFILE = load_profile(None, SESS.reg)
set_profile(PROFILE)
print(f"[mcp] 字段画像：{PROFILE.get('n_fields') if PROFILE else 0} 字段已载入", file=sys.stderr)

# 服务端多会话管理：每个 session_id 持有独立的「门店级工作集(current)」与步骤历史。
# 复用同一个 ENGINE（reg + full_df + compute），不重复加载 <size> MB。
# sessions: sid -> {"current": DataFrame, "steps": [nl, ...], "rows": int}
SESSIONS: dict[str, dict] = {}

PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "semantic_layer",
        "description": "返回本数据集的语义层概览：注册字段数、业务度量(measures)、私有实体(entities)。"
                       "用于在让别的 Agent 理解『哪些业务语义可用』，避免其直接面对物理表。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "explain",
        "description": "给定自然语言需求，返回 Dry-plan 透明执行计划（编译期展开的筛选/聚合/排序/体检步骤），不真正执行。"
                       "用于让调用方在执行前可审查查询意图。",
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "中文自然语言查询"}},
            "required": ["question"],
        },
    },
    {
        "name": "query",
        "description": "给定自然语言需求，经语义层确定性编译后执行，返回结果摘要(行数/列数/类型/体检)与可选 CSV 路径。"
                       "支持**多步连续追问**：传入 session_id 后，同会话的后续查询可在上一步结果(工作集)上再算。"
                       "scope=current 在上一轮结果上操作；scope=full 或在问题里说『全部/整个』回到全量。"
                       "编译期拦截非法字段/算子。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "中文自然语言查询"},
                "out_csv": {"type": "string", "description": "可选，结果 CSV 写出路径"},
                "session_id": {"type": "string",
                               "description": "可选。传入则在服务端建立/复用持久会话，支持连续追问；"
                                              "不传则为无状态单发查询，不污染调用方工作集"},
                "scope": {"type": "string", "enum": ["current", "full"],
                          "description": "可选。current=在上一步工作集上操作；full=在全量数据上操作。"
                                         "省略时由问题关键词(这些/其中 vs 全部/整个)自动推断"},
                "engine": {"type": "string", "enum": ["pandas", "datafusion"],
                           "description": "可选。计算后端：pandas（默认，零依赖）或 datafusion"
                                          "（Apache DataFusion 向量化引擎，适合超内存/高并发）。两者结果等价。"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "validate",
        "description": "给定自然语言需求，仅做语义层编译校验，返回 {ok, errors}，不执行。"
                       "用于在提交正式查询前确认字段/算子合法。",
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "中文自然语言查询"}},
            "required": ["question"],
        },
    },
    {
        "name": "session_reset",
        "description": "清空指定 session_id 的工作集与步骤历史（等价于回到全量数据重新开始）。"
                       "返回该会话新建后的状态。",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string", "description": "要重置的会话 id"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "stat",
        "description": "统计级别查询（第10块 DM 汇总层）：命中预物化汇总表，毫秒级返回分布/排行，"
                       "**不扫 629K 行底表**。适合『XX 的连锁品牌都有哪些，按门店数排序』这类"
                       "先看汇总、再决定下一步的交互。返回每行的下钻过滤键（drill_key），"
                       "客户点选后用 drilldown 工具取原始明细。"
                       f"可用维度：{'/'.join(['省份','城市','规划大区','渠道组','细分渠道','是否连锁','连锁品牌名称'])}。"
                       "度量：cnt(门店数)/avg_rating(均评分)/avg_traffic(均周末客流)/sum_potential(潜力合计)。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_by": {"type": "string",
                             "description": "聚合维度（须是 DM 维度之一，如 连锁品牌名称/渠道组）"},
                "filters": {"type": "object",
                            "description": "可选。{维度: 值} 等值过滤，如 "
                                           '{"城市":"目标城市","渠道组":"餐饮","是否连锁":"是"}'
                                           "（城市口语词自动归一化，如 目标城市→目标城区）"},
                "order_by": {"type": "string", "enum": ["cnt", "avg_rating", "avg_traffic", "sum_potential"],
                             "description": "可选。排序度量，默认 cnt"},
                "ascending": {"type": "boolean", "description": "可选。默认 false（降序）"},
                "limit": {"type": "integer", "description": "可选。返回前 N 行，默认 20"},
                "out_csv": {"type": "string", "description": "可选，结果 CSV 写出路径"},
            },
            "required": ["group_by"],
        },
    },
    {
        "name": "drilldown",
        "description": "下钻查询（第10块）：给定 stat 返回的下钻过滤键（维度=值 精确组合），"
                       "回底表取**原始门店明细**。典型两步流：stat 看品牌排行 → 客户点选某品牌 → "
                       "drilldown({城市:..., 渠道组:..., 连锁品牌名称:...}) 取门店清单。"
                       "底表常驻内存，无需重新读盘。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filters": {"type": "object",
                            "description": '{维度: 值} 精确过滤键，如 {"城市":"目标城市","渠道组":"餐饮",'
                                           '"是否连锁":"是","连锁品牌名称":"瑞幸咖啡"}'},
                "limit": {"type": "integer", "description": "可选。明细行数上限，默认 200"},
                "columns": {"type": "array", "items": {"type": "string"},
                            "description": "可选。返回列白名单；省略时返回常用列"},
                "out_csv": {"type": "string", "description": "可选，明细 CSV 写出路径（写全列）"},
            },
            "required": ["filters"],
        },
    },
    {
        "name": "chart",
        "description": "图形化（第10块 DM + Flint Chart）：先做统计级查询（命中 DM 预物化汇总表，毫秒级、不扫底表），"
                       "再把结果渲染成离线可打开的单文件 HTML 图表（内联 ECharts 运行时，无 CDN 依赖）。"
                       "支持图表类型：单系列 Bar/Line/Scatter/Pie/Donut/Area；多系列分组（传 z 做第二维着色，"
                       "自动按分类色板区分系列）；Heatmap（传 z 且 chart_type=Heatmap）；"
                       "Combo 组合图（柱+线双轴，传 y2 指定第二个度量，如 门店数柱 + 均评分线）。"
                       "调色板按度量语义自动选：计数类(门店数/客流/潜力)→领奖台高亮前3，评分/占比→顺序蓝渐变，"
                       "饼/环/多系列分类→精选 12 色；也可用 palette 参数强制（auto/rank/ramp/single/categorical/none），theme 选亮/暗。"
                       "返回 HTML 路径与摘要，调用方直接打开即可。典型用法：先 stat 看排行 → chart 出图给客户。"
                       f"可用维度：{'/'.join(['省份','城市','规划大区','渠道组','细分渠道','是否连锁','连锁品牌名称'])}。"
                       "度量：cnt(门店数)/avg_rating(均评分)/avg_traffic(均周末客流)/sum_potential(潜力合计)。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_by": {"type": "string",
                             "description": "聚合维度（须是 DM 维度之一，如 连锁品牌名称/渠道组）"},
                "z": {"type": "string",
                      "description": "可选。第二维（分组/着色维度，须是 DM 维度且与 group_by 不同）。"
                                     "传入后做 group_by × z 二维聚合，生成多系列分组图或热力图"},
                "y2": {"type": "string", "enum": ["cnt", "avg_rating", "avg_traffic", "sum_potential"],
                       "description": "可选。组合图（Combo）第二个度量列；传入即生成『柱(y,左轴)+线(y2,右轴)』双轴图。"},
                "filters": {"type": "object",
                            "description": "可选。{维度: 值} 等值过滤，如 "
                                           '{"城市":"目标城市","渠道组":"餐饮","是否连锁":"是"}'},
                "order_by": {"type": "string", "enum": ["cnt", "avg_rating", "avg_traffic", "sum_potential"],
                             "description": "可选。排序（也是默认绘制的度量 y），默认 cnt"},
                "ascending": {"type": "boolean", "description": "可选。默认 false（降序）"},
                "limit": {"type": "integer", "description": "可选。返回前 N 行（主维度分组数），默认 20"},
                "y": {"type": "string", "description": "可选。强制绘制哪个度量列（默认取 order_by；"
                                                       "如想画『按均评分 TOP』传 y=avg_rating）"},
                "chart_type": {"type": "string", "description": "可选。图表类型（Bar Chart/Line Chart/Scatter Plot/"
                                                               "Pie Chart/Donut Chart/Area Chart/Combo Chart/Heatmap…），"
                                                               "不传则按数据自动推导（y2 存在→Combo）"},
                "title": {"type": "string", "description": "可选。图表标题，不传则自动生成"},
                "palette": {"type": "string", "enum": ["auto", "rank", "ramp", "single", "categorical", "none"],
                            "description": "可选。调色板，默认 auto（按度量语义自动选）"},
                "theme": {"type": "string", "enum": ["light", "dark", "none"],
                          "description": "可选。主题，默认 light"},
                "out_html": {"type": "string", "description": "可选。HTML 输出路径，不传则写到 output/charts/"},
            },
            "required": ["group_by"],
        },
    },
    {
        "name": "profile",
        "description": "返回字段统计画像（第5块统计/画像层）。不传 field 返回数据整体画像"
                       "（行数/字段数/关键维度分布）；传 field 返回该字段的真实统计"
                       "（覆盖量/缺失率/基数/Top值/数值范围/按业态覆盖率），"
                       "让外部 Agent 拿到问题前就快速掌握数据形状、按业态路由字段。",
        "inputSchema": {
            "type": "object",
            "properties": {"field": {"type": "string",
                                  "description": "可选。字段名（须是语义层物理字段）；省略则返回整体画像"}},
        },
    },
]


def _semantic_layer() -> dict:
    reg = SESS.reg
    info = {
        "registered_fields": len(reg.fields),
        "measures": list(reg.measures.keys()),
        "entities": list(reg.entities.keys()),
        "sample_fields": list(reg.fields.keys())[:25],
    }
    return {"content": [{"type": "text", "text": json.dumps(info, ensure_ascii=False)}]}


def _explain(args: dict) -> dict:
    plan = explain_query(args["question"], SESS.reg, SESS.full_df)
    return {"content": [{"type": "text", "text": plan}]}


def _get_session(sid: str) -> dict:
    """返回（必要时创建）指定会话的状态字典。current 初始引用全量 DataFrame。"""
    if sid not in SESSIONS:
        SESSIONS[sid] = {"current": SESS.full_df, "steps": [], "rows": len(SESS.full_df)}
    return SESSIONS[sid]


def _query(args: dict) -> dict:
    q_text = args["question"]
    sid = args.get("session_id")
    explicit = args.get("scope")  # "current" | "full" | None

    # ---- 决定作用域（复用会话态关键词，与 CLI 同构）----
    reset = any(w in q_text for w in _RESET_WORDS)
    stay = any(w in q_text for w in _STAY_WORDS)

    if sid:
        st = _get_session(sid)
        if explicit == "current" or (explicit is None and stay and st["steps"]):
            src = st["current"]
            scope = "current"
        elif explicit == "full" or reset:
            src = SESS.full_df
            scope = "full"
            if reset:
                SESSIONS[sid] = {"current": SESS.full_df, "steps": [], "rows": len(SESS.full_df)}
        else:
            src = SESS.full_df
            scope = "full"
    else:
        # 无状态单发：永远在全量上跑，不触碰任何会话工作集
        src = SESS.full_df
        scope = "full"

    in_rows = int(len(src))
    res = SESS.compute(q_text, src, scope=scope, verbose=False,
                       engine=args.get("engine", "pandas"))
    if not res["ok"]:
        return {"content": [{"type": "text", "text": json.dumps(
            {"ok": False, "error": res["error"], "session_id": sid, "scope": scope},
            ensure_ascii=False)}], "isError": True}
    out = res["out"]
    insight_pkg = synthesize(res.get("dsl", {}), out, res.get("audit"), SESS.reg)
    summary = {
        "ok": True, "kind": res["kind"], "rows": int(len(out)), "cols": int(out.shape[1]),
        "columns": res["columns"], "trace": res["trace"], "audit": res["audit"],
        "insight": insight_pkg,
        "session_id": sid, "scope": scope, "input_rows": in_rows,
    }
    # 更新会话工作集：仅 A 类明细成为新工作集
    if sid and res["is_detail"] and len(out):
        SESSIONS[sid]["current"] = src.loc[out.index].copy()
        SESSIONS[sid]["steps"].append(q_text)
        SESSIONS[sid]["rows"] = int(len(out))
        summary["working_set_rows"] = int(len(out))

    if args.get("out_csv"):
        Path(args["out_csv"]).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args["out_csv"], index=False)
        summary["csv"] = args["out_csv"]
    return {"content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}],
            "isError": False}


def _validate(args: dict) -> dict:
    q = auto_parse(args["question"], SESS.reg)
    v = validate_query(q, SESS.reg)
    return {"content": [{"type": "text", "text": json.dumps(v, ensure_ascii=False)}]}


def _session_reset(args: dict) -> dict:
    sid = args["session_id"]
    SESSIONS[sid] = {"current": SESS.full_df, "steps": [], "rows": len(SESS.full_df)}
    return {"content": [{"type": "text", "text": json.dumps(
        {"ok": True, "session_id": sid, "reset": True, "rows": len(SESS.full_df)},
        ensure_ascii=False)}]}


# 下钻明细默认返回的常用列（全列有 152 个，MCP 文本回包只带这些；out_csv 写全列）
_DRILL_DEFAULT_COLS = ["腾讯售点名称", "省份", "城市", "规划大区", "渠道组", "细分渠道",
                       "是否连锁", "连锁品牌名称", "经营场所", "服务状态",
                       "大众点评餐饮点评评分星级", "周末均值客流(人)",
                       "月均收入潜力预测(如有协议)"]


def _stat(args: dict) -> dict:
    """统计级别查询：命中 DM 汇总表（不扫底表）。"""
    db = getattr(SESS, "dm_db", None)
    if not db:
        return {"content": [{"type": "text", "text": json.dumps(
            {"ok": False, "error": "DM 汇总层不可用（构建失败或数据缺失），请改用 query 工具"},
            ensure_ascii=False)}], "isError": True}
    try:
        res, note = query_stat(
            db, group_by=args["group_by"], filters=args.get("filters"),
            order_by=args.get("order_by", "cnt"),
            ascending=bool(args.get("ascending", False)),
            limit=args.get("limit", 20))
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps(
            {"ok": False, "error": str(e), "dm_dimensions": DM_DIMENSIONS},
            ensure_ascii=False)}], "isError": True}

    rows = res.round(3).to_dict(orient="records")
    # 给每行拼好下钻键，客户点选后直接投给 drilldown
    for r in rows:
        r["drill_key"] = {**(args.get("filters") or {}), args["group_by"]: r["dim_value"]}
    summary = {"ok": True, "rows": len(rows), "group_by": args["group_by"],
               "filters": args.get("filters") or {},
               "ignored_filters": note.get("ignored") or [],
               "source": "dm(预物化汇总表, 未扫底表)", "data": rows}
    if note.get("ignored"):
        summary["warning"] = (f"过滤维度 {note['ignored']} 不在 DM 汇总粒度内已被忽略，"
                              "如需这些条件请改用 query 工具（底表扫描）")
    if args.get("out_csv"):
        Path(args["out_csv"]).parent.mkdir(parents=True, exist_ok=True)
        res.to_csv(args["out_csv"], index=False)
        summary["csv"] = args["out_csv"]
    return {"content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}],
            "isError": False}


def _drilldown(args: dict) -> dict:
    """下钻：过滤键 → 底表原始明细（底表常驻内存，不读盘）。"""
    filters = args.get("filters") or {}
    if not filters:
        return {"content": [{"type": "text", "text": json.dumps(
            {"ok": False, "error": "filters 不能为空（会返回整张底表）"},
            ensure_ascii=False)}], "isError": True}
    limit = int(args.get("limit", 200))
    detail = drilldown_df(SESS.full_df, filters, limit=limit)

    cols = args.get("columns") or [c for c in _DRILL_DEFAULT_COLS if c in detail.columns]
    cols = [c for c in cols if c in detail.columns]
    preview = detail[cols].head(20).to_dict(orient="records") if len(detail) else []
    summary = {"ok": True, "rows": int(len(detail)), "limit": limit,
               "filters": filters, "columns": cols,
               "preview_rows": len(preview), "preview": preview}
    if args.get("out_csv"):
        Path(args["out_csv"]).parent.mkdir(parents=True, exist_ok=True)
        detail.to_csv(args["out_csv"], index=False)   # CSV 写全列
        summary["csv"] = args["out_csv"]
    return {"content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}],
            "isError": False}


def _chart(args: dict) -> dict:
    """图形化：统计查询 → Flint spec → 离线 HTML（命中 DM，不扫底表）。

    支持形态：
    - 单维（无 z/y2）：group_by 一维聚合 → Bar/Line/Scatter/Pie/Donut/Area（自动推或显式 chart_type）。
    - 二维（有 z）：group_by × z 交叉聚合 → 多系列分组图（自动按 z 分类着色）或 Heatmap（显式）。
    - 组合图（有 y2）：柱(y,左轴) + 线(y2,右轴) 双轴对比（query_stat 已返回全部度量列，无需额外聚合）。
    """
    db = getattr(SESS, "dm_db", None)
    if not db:
        return {"content": [{"type": "text", "text": json.dumps(
            {"ok": False, "error": "DM 汇总层不可用（构建失败或数据缺失），请改用 query 工具"},
            ensure_ascii=False)}], "isError": True}

    z = args.get("z")
    y2 = args.get("y2")
    measure = args.get("y") or args.get("order_by") or "cnt"
    try:
        if z and not y2:
            # 二维交叉聚合（组合图 y2 优先，此时忽略 z）
            if z not in DM_DIMENSIONS:
                return {"content": [{"type": "text", "text": json.dumps(
                    {"ok": False, "error": f"z={z} 不在 DM 维度 {DM_DIMENSIONS} 内"},
                    ensure_ascii=False)}], "isError": True}
            res, note = query_stat_cross(
                db, group_by=args["group_by"], z=z, filters=args.get("filters"),
                measure=measure, ascending=bool(args.get("ascending", False)),
                limit=args.get("limit", 20))
        else:
            res, note = query_stat(
                db, group_by=args["group_by"], filters=args.get("filters"),
                order_by=args.get("order_by", "cnt"),
                ascending=bool(args.get("ascending", False)),
                limit=args.get("limit", 20))
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps(
            {"ok": False, "error": str(e), "dm_dimensions": DM_DIMENSIONS},
            ensure_ascii=False)}], "isError": True}

    if res.empty:
        return {"content": [{"type": "text", "text": json.dumps(
            {"ok": False, "error": "查询无结果（换个过滤条件试试）"},
            ensure_ascii=False)}], "isError": True}

    # 选度量 y：显式 y > order_by > cnt
    y = measure
    if y not in res.columns:
        y = "cnt"
    # y2 校验（组合图第二个度量，必须是结果里的另一度量列）
    if y2:
        if y2 not in res.columns:
            return {"content": [{"type": "text", "text": json.dumps(
                {"ok": False, "error": f"y2={y2} 不是可用度量（须在 cnt/avg_rating/avg_traffic/sum_potential 中）"},
                ensure_ascii=False)}], "isError": True}
        if y2 == y:
            return {"content": [{"type": "text", "text": json.dumps(
                {"ok": False, "error": "y2 不能与 y 相同（组合图需要两个不同度量）"},
                ensure_ascii=False)}], "isError": True}
    x = "dim_value"
    chart_type = args.get("chart_type")
    if y2 and not chart_type:
        chart_type = "Combo Chart"            # 组合图：柱(y,左轴) + 线(y2,右轴)
    elif z and not chart_type:
        # z 默认 → 多系列分组柱图（按 z 分类着色）；Heatmap 需显式 chart_type 指定
        chart_type = "Bar Chart"

    # 标题：显式 > 组合图 > 二维分组 > 单维 TOP
    if args.get("title"):
        title = args["title"]
    elif y2:
        title = f"{args['group_by']} {y} 与 {y2} 对比"
    elif z:
        title = f"{args['group_by']} × {z} 按 {y}"
    else:
        title = f"{args['group_by']} 按 {y} TOP{args.get('limit', 20)}"

    # 二维时 z 列名为 z_value；组合图/单维无 z
    spec = build_flint_spec(res, x=x, y=y, z=("z_value" if (z and not y2) else None),
                            y2=y2, chart_type=chart_type, title=title)

    # 落盘 spec + 渲染
    CHART_OUT.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(args)
    spec_path = CHART_OUT / f"{safe}.json"
    write_spec(spec, spec_path)

    out_html = args.get("out_html") or str(CHART_OUT / f"{safe}.html")
    palette = args.get("palette", "auto")
    theme = args.get("theme", "light")
    env = {**os.environ, "NODE_PATH": NODE_PATH}
    r = subprocess.run(
        [NODE, str(HERE / "render_flint.mjs"), str(spec_path), out_html, "echarts", palette, theme],
        capture_output=True, text=True, env=env,
    )
    if r.returncode != 0:
        return {"content": [{"type": "text", "text": json.dumps(
            {"ok": False, "error": f"渲染失败: {r.stderr}"}, ensure_ascii=False)}], "isError": True}

    summary = {
        "ok": True, "html": out_html, "title": title, "rows": int(len(res)),
        "group_by": args["group_by"], "measure": y,
        "palette": palette, "palette_hint": spec.get("palette_hint"),
        "theme": theme, "ignored_filters": note.get("ignored") or [],
        "source": "dm(预物化汇总表, 未扫底表)",
    }
    if z:
        summary["z"] = z
        summary["cross"] = True
        summary["series"] = int(res["z_value"].nunique()) if "z_value" in res.columns else 0
    if y2:
        summary["y2"] = y2
        summary["combo"] = True
    if note.get("ignored"):
        summary["warning"] = (f"过滤维度 {note['ignored']} 不在 DM 汇总粒度内已被忽略，"
                              "如需这些条件请改用 query 工具（底表扫描）")
    return {"content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}],
            "isError": False}


def _safe_name(args: dict) -> str:
    """把查询参数压成文件名安全的 slug。"""
    parts = [str(args.get("group_by", "chart"))]
    if args.get("z"):
        parts.append(f"x{args['z']}")
    for k, v in (args.get("filters") or {}).items():
        parts.append(f"{k}_{v}")
    parts.append(str(args.get("order_by", "cnt")))
    if args.get("y2"):
        parts.append(f"y2_{args['y2']}")
    if args.get("chart_type"):
        # 同参数不同图表类型要区分文件名，避免互相覆盖
        ct = args["chart_type"].replace(" ", "")
        parts.append(ct)
    parts.append(f"top{args.get('limit', 20)}")
    slug = "_".join(parts)
    slug = "".join(c if (c.isalnum() or c in "-_") else "_" for c in slug)
    return slug[:120]


def _profile(args: dict) -> dict:
    """返回字段统计画像（第5块）。field 省略→整体画像；指定→单字段统计。"""
    prof = load_profile(None, SESS.reg) or {}
    field = args.get("field")
    if field:
        st = prof.get("fields", {}).get(field)
        if st is None:
            return {"content": [{"type": "text", "text": json.dumps(
                {"error": f"字段 {field!r} 不在画像中"}, ensure_ascii=False)}], "isError": True}
        return {"content": [{"type": "text", "text": json.dumps(
            {"field": field, "stat": st}, ensure_ascii=False)}]}
    summary = {
        "n_rows": prof.get("n_rows"), "n_fields": prof.get("n_fields"),
        "overall": prof.get("overall", {}),
        "field_names": list(prof.get("fields", {}).keys()),
    }
    return {"content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}]}


def handle_call(params: dict) -> dict:
    name = params["name"]
    args = params.get("arguments") or {}
    if name == "semantic_layer":
        return _semantic_layer()
    if name == "explain":
        return _explain(args)
    if name == "query":
        return _query(args)
    if name == "validate":
        return _validate(args)
    if name == "session_reset":
        return _session_reset(args)
    if name == "stat":
        return _stat(args)
    if name == "drilldown":
        return _drilldown(args)
    if name == "chart":
        return _chart(args)
    if name == "profile":
        return _profile(args)
    raise ValueError(f"unknown tool: {name}")


def handle(request: dict):
    method = request.get("method", "")
    rid = request.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "outlet-insight", "version": "1.0.0"},
        }}
    if method == "notifications/initialized":
        return None  # 通知，不回
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            return {"jsonrpc": "2.0", "id": rid, "result": handle_call(params=request["params"])}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def _read_message(rfile) -> dict | None:
    cl = None
    while True:
        line = rfile.readline()
        if line == b"":
            return None
        if line in (b"\n", b"\r\n"):
            break
        if line.lower().startswith(b"content-length:"):
            cl = int(line.split(b":", 1)[1].strip())
    if cl is None:
        return None
    body = rfile.read(cl)
    return json.loads(body)


def _write_message(wfile, msg: dict) -> None:
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    wfile.write(b"Content-Length: %d\r\n\r\n" % len(data))
    wfile.write(data)
    wfile.flush()


def main():
    rfile, wfile = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            msg = _read_message(rfile)
        except Exception as e:
            print(f"[mcp] read error: {e}", file=sys.stderr)
            break
        if msg is None:
            break
        resp = handle(msg)
        if resp is not None:
            _write_message(wfile, resp)


if __name__ == "__main__":
    main()
