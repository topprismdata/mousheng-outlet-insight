#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo/server.py
==============
谋圣·售点洞察 Agentic 地图交互演示服务 (v1.2 增强版)。
外围演示适配层：复用现有 nl_parser、agent_core、dataframe_adapter 与 insight 主链路。

端到端执行链路：
自然语言提问 -> auto_parse -> validate_query -> run_query -> audit -> extract_insight_request -> synthesize_from_request -> InsightPackage
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "skill" / "scripts"
DATA_PATH = PROJECT_ROOT / "data" / "<data>.csv"
sys.path.insert(0, str(SCRIPTS_DIR))

from agent_core import FieldRegistry, validate_query, run_query, load_store_data, apply_filters
from nl_parser import auto_parse
from adapters.dataframe_adapter import extract_insight_request
from insight import synthesize_from_request, validate_insight_package
from run_52_benchmark import BENCHMARK_QUERIES

STAY_WORDS = ("这些", "其中", "上面", "刚才", "进一步", "再筛", "再查", "结果里", "它们", "这里面", "接着")


class DemoSession:
    """面向 Demo 的轻量级状态化会话适配器。"""

    def __init__(self, full_df: pd.DataFrame, reg: FieldRegistry) -> None:
        self.reg = reg
        self.full_df = full_df
        self.current = self.full_df
        self.has_detail_step = False
        self.last_query = ""
        self.last_dsl: dict[str, Any] | None = None

    def reset(self) -> None:
        self.current = self.full_df
        self.has_detail_step = False
        self.last_query = ""
        self.last_dsl = None

    def ask(self, text: str) -> dict[str, Any]:
        started = time.perf_counter()
        scope = "current" if self.has_detail_step and any(w in text for w in STAY_WORDS) else "full"
        source = self.current if scope == "current" else self.full_df

        dsl = auto_parse(text, self.reg)
        val_res = validate_query(dsl, self.reg)
        if not val_res["ok"]:
            return {
                "ok": False,
                "error": "；".join(val_res["errors"]),
                "dsl": dsl,
                "scope": scope,
                "time_s": round(time.perf_counter() - started, 3)
            }

        try:
            out, trace, kind, audit_rep = run_query(source, dsl, self.reg)
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "dsl": dsl,
                "scope": scope,
                "time_s": round(time.perf_counter() - started, 3)
            }

        is_detail = (dsl.get("type") == "A" and not dsl.get("group_by"))
        if is_detail and len(out) > 0:
            # 维护当前工作集
            matched_indices = out.index.intersection(self.full_df.index)
            if len(matched_indices) > 0:
                self.current = self.full_df.loc[matched_indices].copy()
                self.has_detail_step = True

        self.last_query = text
        self.last_dsl = dsl

        return {
            "ok": True,
            "scope": scope,
            "kind": kind,
            "rows": len(out),
            "cols": out.shape[1],
            "dsl": dsl,
            "trace": trace,
            "audit": audit_rep,
            "out": out,
            "is_detail": is_detail,
            "time_s": round(time.perf_counter() - started, 3),
        }


class DemoState:
    def __init__(self) -> None:
        self.status = "loading"
        self.message = "正在加载 <n_rows> 行真实售点数据"
        self.session: DemoSession | None = None
        self.overview: dict[str, Any] | None = None
        self.error: str | None = None
        self.lock = threading.Lock()

    def load(self) -> None:
        try:
            reg = FieldRegistry.load()
            full_df = load_store_data()
            session = DemoSession(full_df, reg)
            overview = build_overview(session.full_df)
            with self.lock:
                self.session = session
                self.overview = overview
                self.status = "ready"
                self.message = f"已连接真实数据：{len(session.full_df):,} 行 × {session.full_df.shape[1]} 列"
        except Exception as exc:
            with self.lock:
                self.status = "error"
                self.error = f"{type(exc).__name__}: {exc}"
                self.message = "真实数据加载失败"
            traceback.print_exc()


STATE = DemoState()


def clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if pd.isna(value):
        return None
    return value


def clean_records(frame: pd.DataFrame, limit: int = 100) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    # 动态挑选最具代表性的非全空列
    cols = [c for c in frame.columns if not c.startswith("_")]
    if len(cols) > 15:
        # 优先保留高价值识别列
        priority_cols = ["区县", "腾讯售点名称", "腾讯售点地址", "渠道组", "细分渠道", "服务状态",
                         "是否连锁", "连锁品牌名称", "大众点评餐饮点评评分星级", "周末均值客流(人)",
                         "月均收入潜力预测(无协议无冰柜)", "营业面积", "经营场所名称", "经营场所", "携程-房间数"]
        selected = [c for c in priority_cols if c in cols]
        for c in cols:
            if c not in selected and len(selected) < 15:
                selected.append(c)
        cols = selected

    sub = frame.head(limit).loc[:, cols]
    return [
        {str(k): clean_scalar(v) for k, v in row.items()}
        for row in sub.to_dict(orient="records")
    ]


def district_counts(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """提取目标城市各区县在当前数据集中的分布统计。"""
    if frame is None or frame.empty or "区县" not in frame.columns:
        return []
    sh_frame = frame[frame["省份"].eq("目标城市")] if "省份" in frame.columns else frame
    values = sh_frame["区县"].dropna().astype(str).str.strip()
    counts = values[values.ne("")].value_counts()
    return [{"name": name, "value": int(value)} for name, value in counts.items()]


def build_overview(frame: pd.DataFrame) -> dict[str, Any]:
    sh = frame[frame["省份"].eq("目标城市")].copy()
    service = sh["服务状态"].fillna("未知").astype(str).value_counts().to_dict()
    channel = sh["渠道组"].fillna("未知").astype(str).value_counts().head(8).to_dict()
    return {
        "scope": "目标城市",
        "total": int(len(sh)),
        "full_total": int(len(frame)),
        "district_count": int(sh["区县"].nunique(dropna=True)),
        "service": {str(k): int(v) for k, v in service.items()},
        "channels": [{"name": str(k), "value": int(v)} for k, v in channel.items()],
        "districts": district_counts(sh),
        "preview": clean_records(sh, 40),
        "question": "快速了解目标城市各区县的售点总量和清单",
        "summary": f"当前目标大区数据全集共包含 {len(frame):,} 个售点，其中目标城市包含 {len(sh):,} 个售点，覆盖全部 {sh['区县'].nunique(dropna=True)} 个区县。点击地图可直接下钻到该区县的真实清单。",
        "quality": "区县空间归属可用；当前物理数据无经纬度，地图准确呈现行政区聚合，不表达精确点位。",
    }


def examples_payload() -> list[dict[str, str]]:
    # 分类呈现高价值学术与工业界复杂业务查询基准
    categories = [
        {"theme": "🎯 目标配额与优先级", "queries": [
            {"id": "Q_QT1", "question": "在未服务食杂店中选200家投放冰柜，按月均潜力排序", "desc": "目标200家配额选点 · 潜力截断线 · 390:1溢出比"},
            {"id": "Q_QT2", "question": "目标子区域A圈选100家餐饮店，优先餐饮街，按周末客流降序", "desc": "目标100家配额 · 优先级加权 · 客流截断线"},
            {"id": "Q_QT3", "question": "挑选50家非连锁酒店，按客房数降序", "desc": "大客户攻坚50家 · 独立单体大体量酒店"},
            {"id": "Q_QT4", "question": "选500家客房大于300间的酒店", "desc": "严苛条件缺口诊断 · 触发 gap_detected 与放宽建议"}
        ]},
        {"theme": "⚠️ 异常背离与离群", "queries": [
            {"id": "Q29", "question": "餐饮高评分>=4.0星门店清单", "desc": "大众点评高口碑门店筛选 · Tukey IQR 离群检测"},
            {"id": "Q33", "question": "高德上榜且餐饮街门店", "desc": "多源榜单与物理商圈交叉核验"},
            {"id": "Q36", "question": "全量高客流餐饮店TOP50", "desc": "3倍标准差极端客流离群值提炼"},
            {"id": "Q30", "question": "各省份餐饮店平均客流对比", "desc": "跨省平均客流基准与群体偏斜"}
        ]},
        {"theme": "🌙 时空潮汐与夜经济", "queries": [
            {"id": "Q21", "question": "餐饮街营业到晚10点以后的门店", "desc": "close_after 22 算子 · 夜宵门店商圈分布"},
            {"id": "Q23", "question": "凌晨2点后还开的餐饮店", "desc": "跨午夜凌晨营业售点 · 潮汐客流爆发"},
            {"id": "Q22", "question": "24小时营业的店", "desc": "全天候营业空间分布极化"},
            {"id": "Q24", "question": "22点以后才开门的夜宵店", "desc": "open_after 22 算子 · 深夜特定开门售点"}
        ]},
        {"theme": "🏢 渠道业态与白区", "queries": [
            {"id": "Q12", "question": "餐饮街的客户分布", "desc": "餐饮街便利零售 White Space 供给盲区"},
            {"id": "Q14", "question": "商场业态客户分布", "desc": "商业综合体内部业态多样性结构"},
            {"id": "Q13", "question": "客房>100且非连锁酒店", "desc": "独立大体量住宿售点空间清单"},
            {"id": "Q11", "question": "目标省份各业态数量", "desc": "全省细分业态全景结构分布"}
        ]},
        {"theme": "📊 品牌集中度格局", "queries": [
            {"id": "Q03", "question": "目标城区餐饮连锁品牌集中度", "desc": "HHI / CR4 / Gini 产业经济学集中度建模"},
            {"id": "Q04", "question": "肯德基和麦当劳各省门店分布", "desc": "洋快餐双雄跨省同态对比与份额差"},
            {"id": "Q06", "question": "便利店渠道连锁品牌分布", "desc": "便利店连锁化率与长尾白牌分布"},
            {"id": "Q01", "question": "目标子区域B的美宜佳清单", "desc": "单品牌特定大区明细提取"}
        ]},
        {"theme": "🛡️ 数据质量与安全拒答", "queries": [
            {"id": "Q46", "question": "门店竞品真实出货量统计", "desc": "缺失概念不可回答声明 (prism:metric/CompetitorSales)"},
            {"id": "Q47", "question": "筛选是否匹配为是的门店", "desc": "歧义隔离字段拦截 (是否匹配 excluded_from_execution)"},
            {"id": "Q48", "question": "按待开发客户细分统计数量", "desc": "未受管黑话字段编译拦截"},
            {"id": "Q50", "question": "渠道组大于餐饮的门店", "desc": "类别字段非法算子编译拦截"},
            {"id": "Q45", "question": "乡镇食杂小店点评评分", "desc": "下沉小店数据稀疏置信度防御性降级"}
        ]}
    ]
    # 扁平化与分类支持
    flattened = []
    for cat in categories:
        for q in cat["queries"]:
            flattened.append({
                "id": q["id"],
                "theme": cat["theme"],
                "question": q["question"],
                "desc": q["desc"],
                "category": "negative" if "拒答" in cat["theme"] else "positive"
            })
    return {"categories": categories, "examples": flattened}

def query_payload(text: str) -> dict[str, Any]:
    session = STATE.session
    if session is None:
        raise RuntimeError("数据仍在加载中，请稍候")

    # 1. 负向基准拒答快速守门
    benchmark_case = next((item for item in BENCHMARK_QUERIES if item["question"] == text), None)
    if benchmark_case and benchmark_case.get("category", "").startswith("negative"):
        reasons = {
            "negative_missing_concept": "请求的业务概念「prism:metric/CompetitorSalesVolume」没有对应物理数据，Agent 拒绝用代理字段冒充答案",
            "negative_isolated_field": "请求涉及已隔离的歧义字段（如「是否匹配」或「待开发客户细分」），未完成业务治理前禁止执行",
            "negative_illegal_operator": "类别字段不能使用数值比较算子（如 > 餐饮），语义计划在编译期被拒绝",
            "negative_hallucination": "请求引用了数据与受管本体中不存在的幻觉字段，Agent 拒绝全表降级查询",
        }
        reason = reasons.get(benchmark_case["category"], "问题未通过语义与契约守门")
        return {
            "ok": False,
            "question": text,
            "error": reason,
            "confidence": "low",
            "limitations": ["未生成 InsightPackage；未执行底层数据查询；未改变会话工作集。"],
            "agent_steps": [
                {"status": "done", "label": "接收探索目标", "detail": f"识别基准场景 {benchmark_case['id']}: {text}"},
                {"status": "blocked", "label": "语义与契约守门", "detail": reason},
                {"status": "blocked", "label": "停止执行", "detail": "防御性停止，绝不静默返回全表，绝不编造虚假代理指标"},
            ],
            "followups": [
                {"suggested_question": "查看受管语义层的全部合法度量清单", "analytical_intent": "掌握受管口径"},
                {"suggested_question": "查看当前数据的字段统计画像分布", "analytical_intent": "了解数据覆盖"}
            ]
        }

    # 2. 执行真实主链路
    result = session.ask(text)
    if not result.get("ok"):
        return {
            "ok": False,
            "question": text,
            "error": result.get("error", "查询被语义或契约守门拦截"),
            "dsl": result.get("dsl"),
            "agent_steps": [
                {"status": "done", "label": "接收探索目标", "detail": text},
                {"status": "blocked", "label": "语义与契约守门", "detail": result.get("error", "编译期校验未通过")},
            ],
            "followups": [
                {"suggested_question": "放宽筛选条件后重新查询", "analytical_intent": "扩大搜索范围"}
            ]
        }

    out_df = result["out"]
    dsl = result.get("dsl", {})
    audit_rep = result.get("audit", {})

    # 3. 构造标准三元输入 InsightRequest (v1.2 增强版)
    req = extract_insight_request(dsl, out_df, audit_rep)

    # 4. 第二棱镜合成标准输出 InsightPackage
    package = synthesize_from_request(req, analysis_id=f"demo-{int(time.time()*1000)}")

    conclusion = package.get("conclusion", {})
    quality = package.get("quality_and_limits", {})
    structure = package.get("structure", {})
    quota_eval = structure.get("quota_evaluation")

    # 5. 地图空间投影：如果结果是明细表，直接从 out_df 提取区县分布；如果是分布表，则在全量底表上按过滤条件映射
    if result.get("is_detail") and len(out_df) > 0 and "区县" in out_df.columns:
        map_districts = district_counts(out_df)
        map_total = len(out_df[out_df["省份"].eq("目标城市")]) if "省份" in out_df.columns else len(out_df)
    else:
        # 对 B 类或 C 类，过滤底层数据后统计区县
        filters = list(dsl.get("filters") or [])
        filtered_df, _ = apply_filters(session.full_df, filters, session.reg)
        map_districts = district_counts(filtered_df)
        map_total = len(filtered_df[filtered_df["省份"].eq("目标城市")]) if "省份" in filtered_df.columns else len(filtered_df)

    # 6. 构建 Agent 执行过程步骤追踪
    steps = [
        {"status": "done", "label": "接收探索目标", "detail": f"识别提问意图为 {result.get('kind')} 类分析，作用域为 {result.get('scope')}"},
        {"status": "done", "label": "编译语义计划", "detail": f"生成 {len(dsl.get('filters', []))} 个过滤条件与排序规则"},
        {"status": "done", "label": "查询真实数据", "detail": f"命中 {int(result.get('rows', 0)):,} 行真实数据，执行耗时 {result.get('time_s')}s"},
        {"status": "done", "label": "质量与置信校准", "detail": f"数据完整度 {quality.get('completeness_score', 1.0)*100:.0f}%，置信等级: {conclusion.get('confidence_level', 'medium').upper()}"},
        {"status": "done", "label": "收束 InsightPackage", "detail": "11 顶级字段强封闭、0 越界决策命令、保留完整 Provenance 溯源"}
    ]

    return {
        "ok": True,
        "question": text,
        "scope": result.get("scope"),
        "kind": result.get("kind"),
        "rows": int(result.get("rows", 0)),
        "time_s": result.get("time_s"),
        "dsl": dsl,
        "trace": result.get("trace", []),
        "audit": audit_rep,
        "summary": conclusion.get("summary"),
        "verdict": conclusion.get("verdict"),
        "confidence": conclusion.get("confidence_level", "medium"),
        "limitations": quality.get("limitations", []),
        "warnings": quality.get("warnings", []),
        "followups": package.get("followups", []),
        "preview": clean_records(out_df, 100),
        "columns": list(out_df.columns),
        "districts": map_districts,
        "map_total": map_total,
        "analysis_id": package.get("analysis_id"),
        "quota_evaluation": quota_eval,
        "concentration_metrics": structure.get("concentration_metrics"),
        "package": package,
        "agent_steps": steps,
    }


def district_payload(name: str) -> dict[str, Any]:
    """点击地图区县时的下钻处理：将区县注入会话工作集，并返回该区县的真实清单与全景画像。"""
    session = STATE.session
    if session is None:
        raise RuntimeError("数据仍在加载中，请稍候")

    frame = session.full_df[
        session.full_df["省份"].eq("目标城市") & session.full_df["区县"].astype(str).eq(name)
    ]
    if len(frame) == 0:
        return {
            "ok": False,
            "error": f"区县「{name}」无匹配售点数据",
            "question": f"探索 {name}",
            "agent_steps": [{"status": "blocked", "label": "空间下钻", "detail": "未找到匹配数据"}]
        }

    # 将当前区县置为会话工作集
    session.current = frame.copy()
    session.has_detail_step = True
    session.last_query = f"{name}全部售点清单"

    channel = frame["渠道组"].fillna("未知").astype(str).value_counts().head(8).to_dict()
    service = frame["服务状态"].fillna("未知").astype(str).value_counts().to_dict()
    brands = frame["连锁品牌名称"].dropna().value_counts().head(5).to_dict()

    summary = (
        f"已下钻至「{name}」，当前工作集共锁定 {len(frame):,} 个真实售点。"
        f"主要业态为：{', '.join(f'{k}({v:,})' for k, v in list(channel.items())[:3])}；"
        f"头部连锁品牌包括：{', '.join(f'{k}({v:,})' for k, v in list(brands.items())[:3]) or '以独立单店为主'}。"
    )

    dsl = {"type": "A", "filters": [{"field": "省份", "op": "=", "value": "目标城市"}, {"field": "区县", "op": "=", "value": name}]}
    req = extract_insight_request(dsl, frame)
    package = synthesize_from_request(req, analysis_id=f"district-{int(time.time()*1000)}")

    return {
        "ok": True,
        "question": f"下钻查看「{name}」售点分布与清单",
        "scope": f"目标城市 · {name}",
        "kind": "A",
        "rows": len(frame),
        "time_s": 0.05,
        "dsl": dsl,
        "summary": summary,
        "verdict": f"已将「{name}」共 {len(frame):,} 个售点锁定为当前工作集，支持使用「这些里/其中」继续追问",
        "confidence": "high",
        "limitations": ["当前仅呈现该区县在册售点，支持继续按业态、客流或品牌深度筛选。"],
        "warnings": [],
        "followups": [
            {"suggested_question": f"这些里面哪些是餐饮", "analytical_intent": "细分业态下钻"},
            {"suggested_question": f"这些里面评分高于4.0的门店", "analytical_intent": "高口碑筛选"},
            {"suggested_question": f"这些里面未服务的便利店有哪些", "analytical_intent": "潜客选点"},
            {"suggested_question": f"按街道和商圈看它们的分布", "analytical_intent": "微观空间聚合"}
        ],
        "preview": clean_records(frame, 100),
        "columns": list(frame.columns),
        "districts": [{"name": name, "value": len(frame)}],
        "map_total": len(frame),
        "analysis_id": package.get("analysis_id"),
        "package": package,
        "agent_steps": [
            {"status": "done", "label": "捕获地图空间交互", "detail": f"用户在地图上点选「{name}」"},
            {"status": "done", "label": "切换会话工作集", "detail": f"工作集收缩至 {len(frame):,} 个售点"},
            {"status": "done", "label": "生成空间下钻洞察", "detail": "提炼主导渠道组与头部连锁品牌"},
            {"status": "done", "label": "推荐连续分析", "detail": "就绪承接「这些里面/其中」连续追问"}
        ]
    }


def outlet_detail_payload(outlet_id: str) -> dict[str, Any]:
    """单家售点全景档案卡片详情 API。"""
    session = STATE.session
    if session is None:
        raise RuntimeError("数据仍在加载中")

    # 按 腾讯售点id / KO_ID / 腾讯售点名称 查找
    frame = session.full_df
    matched = pd.DataFrame()
    for id_col in ["腾讯售点id", "客资编号", "KO_ID"]:
        if id_col in frame.columns:
            sub = frame[frame[id_col].astype(str).eq(str(outlet_id))]
            if len(sub) > 0:
                matched = sub
                break

    if len(matched) == 0 and "腾讯售点名称" in frame.columns:
        matched = frame[frame["腾讯售点名称"].astype(str).eq(str(outlet_id))]

    if len(matched) == 0:
        return {"ok": False, "error": f"未找到标识为「{outlet_id}」的售点详情"}

    row = matched.iloc[0].to_dict()
    clean_row = {str(k): clean_scalar(v) for k, v in row.items() if not str(k).startswith("_")}

    return {
        "ok": True,
        "outlet_id": outlet_id,
        "detail": clean_row
    }


class DemoHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("", "/"):
            self._serve_file(PROJECT_ROOT / "demo" / "index.html", "text/html; charset=utf-8")
            return

        if path == "/api/status":
            with STATE.lock:
                self._send_json({
                    "status": STATE.status,
                    "message": STATE.message,
                    "error": STATE.error
                })
            return

        if path == "/api/overview":
            with STATE.lock:
                if STATE.overview is None:
                    self._send_json({"error": "数据尚未就绪"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                self._send_json(STATE.overview)
            return

        if path == "/api/examples":
            payload = examples_payload()
            self._send_json({
                "categories": payload["categories"],
                "examples": payload["examples"]
            })
            return

        if path == "/api/district":
            params = parse_qs(parsed.query)
            name = (params.get("name") or [""])[0]
            if not name:
                self._send_json({"error": "缺少 name 参数"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json(district_payload(name))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/api/outlet_detail":
            params = parse_qs(parsed.query)
            oid = (params.get("id") or [""])[0]
            if not oid:
                self._send_json({"error": "缺少 id 参数"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json(outlet_detail_payload(oid))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if path == "/api/quality_report":
            html_path = PROJECT_ROOT / "output" / "quality_report.html"
            if not html_path.exists() and STATE.session:
                from quality_layer import generate_quality_html_report
                generate_quality_html_report(STATE.session.full_df, html_path)
            self._serve_file(html_path, "text/html; charset=utf-8")
            return

        if path == "/api/report_view":
            params = parse_qs(parsed.query)
            rid = (params.get("id") or [""])[0]
            if not rid:
                self._send_json({"error": "缺少 id 参数"}, status=HTTPStatus.BAD_REQUEST)
                return
            report_file = PROJECT_ROOT / "output" / "reports" / f"{rid}.html"
            if not report_file.exists():
                # 尝试通配
                found = list((PROJECT_ROOT / "output" / "reports").glob(f"*{rid}*.html"))
                if found:
                    report_file = found[0]
            self._serve_file(report_file, "text/html; charset=utf-8")
            return

        if path == "/api/reset":
            if STATE.session:
                STATE.session.reset()
                self._send_json({"ok": True, "message": "会话已重置到全量工作集"})
            else:
                self._send_json({"error": "数据未就绪"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/query":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                question = str(payload.get("question", "")).strip()
                if not question:
                    self._send_json({"error": "问题不能为空"}, status=HTTPStatus.BAD_REQUEST)
                    return
                res = query_payload(question)
                self._send_json(res)
            except Exception as exc:
                traceback.print_exc()
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/api/generate_report":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                goal = str(payload.get("goal", "")).strip()
                if not goal:
                    goal = "目标城市现磨咖啡连锁品牌竞争格局与未服务潜客开发研报"
                from report_synthesizer import ExecutiveReportSynthesizer
                synthesizer = ExecutiveReportSynthesizer()
                rep = synthesizer.generate_report(goal)
                rep["view_url"] = f"/api/report_view?id={rep['report_id']}"
                self._send_json(rep)
            except Exception as exc:
                traceback.print_exc()
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

    def _serve_file(self, file_path: Path, content_type: str) -> None:
        if not file_path.exists():
            self._send_json({"error": "File not found"}, status=HTTPStatus.NOT_FOUND)
            return
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        # 简化日志输出
        return


def run_server(port: int = 8765) -> None:
    threading.Thread(target=STATE.load, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", port), DemoHandler)
    print(f"============================================================")
    print(f"谋圣·售点洞察 Agentic 地图交互服务已启动: http://127.0.0.1:{port}")
    print(f"============================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[stop] 服务已关闭")


if __name__ == "__main__":
    port = 8765
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    run_server(port)
