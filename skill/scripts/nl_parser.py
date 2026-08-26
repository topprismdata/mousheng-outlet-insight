# -*- coding: utf-8 -*-
"""
nl_parser.py
============
自然语言 → 确定性 DSL 解析器（含业务实体/度量中介 + 目标容量配额与优先级阶梯识别）。

支持：
1. A 类（明确查询清单）：品牌、大区/城市、业态、房型/评分阈值、目标配额（如"选100家/要200家"）、优先级漏斗（"优先餐饮街/按客流降序"）
2. B 类（分布探索与集中度分析）：按 [连锁品牌名称, 规划大区, 省份, 城市, 渠道组, 经营场所] 分组统计
3. C 类（综合规则排行）：多条规则达标率、规模基数与分层
4. 第一棱镜 Analysis Intent 编译：对象、范围、度量、规则、目标配额、歧义隔离声明
"""
from __future__ import annotations

import json
import re
from typing import Any
import pandas as pd

from agent_core import FieldRegistry, validate_query, _expand_semantics
from profiler import route_metric
from bizhours import parse_time_predicate


# ----------------------------- 语义探测：度量 / 实体 -----------------------------
def _detect_semantic(text: str, reg: FieldRegistry) -> dict:
    """探测文本中命中的度量和实体，返回 {'measures': [...], 'entities': [...], 'suppress': [...]}"""
    measures_hit: list[str] = []
    entities_hit: list[str] = []
    suppress: list[str] = []

    # 优先匹配最长词（"餐饮高评分" > "餐饮"）
    for mname in sorted(reg.measures.keys(), key=len, reverse=True):
        if mname in text:
            measures_hit.append(mname)
            meta = reg.measures[mname]
            if "suppress" in meta:
                suppress.extend(meta["suppress"])

    for ename in sorted(reg.entities.keys(), key=len, reverse=True):
        if ename in text:
            entities_hit.append(ename)
            meta = reg.entities[ename]
            if "suppress" in meta:
                suppress.extend(meta["suppress"])

    return {"measures": measures_hit, "entities": entities_hit, "suppress": suppress}


def _dedup_filters(filters: list[dict]) -> list[dict]:
    """过滤条件去重（按 field + op + value）。"""
    seen = set()
    res = []
    for f in filters:
        key = (f.get("field"), f.get("op"), str(f.get("value")))
        if key not in seen:
            seen.add(key)
            res.append(f)
    return res


# ----------------------------- A 类解析 -----------------------------
def parse_A(text: str, reg: FieldRegistry) -> dict:
    """明确查询解析。返回 {type:'A', filters, order_by, limit, target_quota, priority_funnel, ...}"""
    sem = _detect_semantic(text, reg)
    q: dict[str, Any] = {
        "type": "A",
        "filters": [],
        "order_by": [],
        "limit": None,
        "target_quota": None,
        "priority_funnel": []
    }

    # —— 1. 地理空间过滤 ——
    if "上海市" in text or "上海" in text or "上海城区" in text:
        q["filters"].append({"field": "省份", "op": "=", "value": "上海市"})
    elif "江苏省" in text or "江苏" in text:
        q["filters"].append({"field": "省份", "op": "=", "value": "江苏省"})
    elif "浙江省" in text or "浙江" in text:
        q["filters"].append({"field": "省份", "op": "=", "value": "浙江省"})

    big_areas = ["浦西大区", "浦东大区", "苏州大区", "无锡大区", "昆太大区", "嘉兴大区", "南通大区"]
    # 仅当文本明确指定某个具体大区（且不是"各大区/大区分布/所有大区"）时才添加大区过滤
    if not any(k in text for k in ["各大区", "大区分", "所有大区", "每个大区"]):
        for a in big_areas:
            if a in text or a.replace("大区", "") + "大区" in text:
                q["filters"].append({"field": "规划大区", "op": "=", "value": a})
                break

    # —— 2. 业态 ——
    channel_map = {
        "便利店": "便利店", "食杂": "食杂", "零售店": "食杂", "超市": "小型超市",
        "餐饮": "餐饮", "餐厅": "餐饮", "饭店": "餐饮", "餐馆": "餐饮",
        "住宿": "住宿", "酒店": "住宿", "宾馆": "住宿", "民宿": "住宿",
        "景区": "旅游", "景点": "旅游", "乐园": "旅游",
        "教育": "教育", "学校": "教育", "大学": "教育", "中学": "教育",
        "运动": "运动场所", "健身": "运动场所", "娱乐": "娱乐", "KTV": "娱乐",
    }
    if "餐饮街" not in text:
        for k, v in channel_map.items():
            if k in text and k not in sem["suppress"]:
                q["filters"].append({"field": "渠道组", "op": "=", "value": v})
                break

    # —— 3. 品牌 ——
    exclude_brand = set(channel_map) | set(big_areas) | {
        "非连锁", "独立运营", "独立", "国营", "国有", "政府", "联合管理", "联合",
        "半封闭", "连锁", "上榜", "名单", "客户", "门店", "清单", "潜力",
        "TOP", "top", "前", "选", "挑选", "圈选", "要", "找", "拓", "投放",
        "各大区", "大区", "夜宵", "大型", "大型酒", "大型酒店", "周边", "集中度",
        "分布", "渠道", "食杂店", "便利店", "小店", "售点", "客户", "门店",
        "江苏", "浙江", "上海", "广东", "北京"
    }

    # 优先匹配明确品牌
    brand_found = False
    for b in ["美宜佳", "瑞幸咖啡", "瑞幸", "巴比馒头", "正新鸡排", "肯德基", "麦当劳", "星巴克", "华莱士", "蜜雪冰城", "罗森", "全家", "零食很嗨"]:
        if b in text:
            # 归一化瑞幸
            val_b = "瑞幸咖啡" if b == "瑞幸" else b
            q["filters"].append({"field": "连锁品牌名称", "op": "contains", "value": val_b})
            brand_found = True
            break

    if not brand_found:
        m = re.search(r"的([\u4e00-\u9fa5A-Za-z0-9]{2,8})(清单|门店|店|加盟)", text)
        if m and m.group(1) not in exclude_brand:
            q["filters"].append({"field": "连锁品牌名称", "op": "contains", "value": m.group(1)})

    # —— 4. 连锁 / 非连锁 ——
    if "非连锁" in text or "单体" in text or "独立" in text:
        q["filters"].append({"field": "是否连锁", "op": "=", "value": "否"})
    elif "连锁" in text and "非连锁" not in text and not brand_found and "集中度" not in text:
        q["filters"].append({"field": "是否连锁", "op": "=", "value": "是"})

    # —— 5. 经营场所 ——
    if "餐饮街" in text or "美食街" in text or "小吃街" in text:
        q["filters"].append({"field": "经营场所", "op": "contains", "value": "餐饮街"})
    elif "商场" in text or "购物中心" in text or "百货" in text:
        q["filters"].append({"field": "经营场所", "op": "contains", "value": "商场"})
    elif "社区" in text or "小区" in text:
        q["filters"].append({"field": "经营场所", "op": "contains", "value": "社区"})
    elif "写字楼" in text or "办公" in text:
        q["filters"].append({"field": "经营场所", "op": "contains", "value": "写字楼"})
    elif "交通枢纽" in text or "火车站" in text or "机场" in text:
        q["filters"].append({"field": "经营场所", "op": "contains", "value": "交通枢纽"})

    # —— 6. 房型 / 评分 / 面积数值过滤 ——
    m = re.search(r"(?:客房|房间|房型)(?:数)?\s*(?:>|>=|超过|大于|不低于|不少于)?\s*(\d+)", text)
    if m:
        val = int(m.group(1))
        q["filters"].append({"field": "携程-房间数", "op": ">=", "value": val})

    m_score = re.search(r"(?:评分|星级)\s*(?:>|>=|超过|大于|不低于|不少于)?\s*(\d+\.?\d*)", text)
    if m_score and "高评分" not in text:
        s_val = float(m_score.group(1))
        q["filters"].append({"field": "大众点评餐饮点评评分星级", "op": ">=", "value": s_val})

    m_area = re.search(r"(?:营业)?面积\s*(?:>|>=|超过|大于|不低于|不少于)?\s*(\d+)", text)
    if m_area:
        a_val = float(m_area.group(1))
        q["filters"].append({"field": "营业面积", "op": ">=", "value": a_val})

    # —— 7. 服务状态 ——
    if "未服务" in text:
        q["filters"].append({"field": "服务状态", "op": "=", "value": "未服务"})

    # —— 8. 目标容量配额 (Target Quota) ——
    target_n = None
    m_quota = re.search(r"(?:选|挑选|圈选|要|找|拓|投放|首发|发展|新增)\s*(\d+)\s*(?:家|个|间|户|处)?|前\s*(\d+)|TOP\s*(\d+)|top\s*(\d+)|(\d+)\s*(?:家|个|间|户)(?:店|售点|客户|潜客|便利店|餐厅|酒店)?", text)
    if m_quota:
        target_n = next((int(g) for g in m_quota.groups() if g), None)
    if target_n and target_n > 0:
        q["limit"] = target_n
        q["target_quota"] = target_n

    # —— 9. 优先级排序与漏斗 ——
    priority_funnel = []
    if "优先餐饮街" in text or "优先选餐饮街" in text:
        priority_funnel.append({"field": "经营场所", "op": "contains", "value": "餐饮街", "tier": "P1", "weight": 0.4})
    elif "优先商场" in text:
        priority_funnel.append({"field": "经营场所", "op": "contains", "value": "商场", "tier": "P1", "weight": 0.4})

    if "潜力" in text:
        q["order_by"].append({"field": "月均收入潜力预测(无协议无冰柜)", "dir": "desc"})
        priority_funnel.append({"field": "月均收入潜力预测(无协议无冰柜)", "order": "desc", "tier": "P1", "weight": 0.4})
    if "客流" in text:
        q["order_by"].append({"field": "周末均值客流(人)", "dir": "desc"})
        priority_funnel.append({"field": "周末均值客流(人)", "order": "desc", "tier": "P1", "weight": 0.3})
    if "客房" in text and ("排序" in text or "降序" in text or "选" in text or "TOP" in text or "top" in text):
        q["order_by"].append({"field": "携程-房间数", "dir": "desc"})
        priority_funnel.append({"field": "携程-房间数", "order": "desc", "tier": "P1", "weight": 0.4})
    if "评分" in text and ("排序" in text or "降序" in text):
        q["order_by"].append({"field": "大众点评餐饮点评评分星级", "dir": "desc"})

    q["priority_funnel"] = priority_funnel

    # —— 语义层展开 ——
    for mname in sem["measures"]:
        mm = reg.resolve_measure(mname)
        if mm and "op" in mm:
            q["filters"].append({"field": mm["field"], "op": mm["op"], "value": mm["value"]})
        elif mm:
            q["filters"].append({"field": mm["field"]})
    for ename in sem["entities"]:
        ee = reg.resolve_entity(ename)
        if ee:
            q["filters"].append({"field": ee["field"], "op": ee.get("op", "="), "value": ee["value"]})

    # —— 10. 营业时间谓词 ——
    tp = parse_time_predicate(text)
    if tp:
        q["filters"].append(tp)

    q["filters"] = _dedup_filters(q["filters"])
    return q


# ----------------------------- B 类解析 -----------------------------
def parse_B(text: str, reg: FieldRegistry) -> dict:
    """分布探索与集中度分析解析。"""
    q = parse_A(text, reg)
    q["type"] = "B"
    q["group_by"] = []
    q["metrics"] = [{"name": "客户数", "agg": "count"}]

    # 集中度分析优先按连锁品牌聚合
    if "集中度" in text or "垄断" in text or "品牌集中度" in text:
        q["group_by"].append("连锁品牌名称")
    elif "品牌" in text and "各业态" in text:
        q["group_by"].extend(["渠道组", "连锁品牌名称"])
    elif "连锁品牌" in text and ("分布" in text or "结构" in text):
        q["group_by"].append("连锁品牌名称")

    if "大区" in text:
        if "规划大区" not in q["group_by"]:
            q["group_by"].append("规划大区")
    elif "省" in text:
        if "省份" not in q["group_by"]:
            q["group_by"].append("省份")
    elif "城市" in text:
        if "城市" not in q["group_by"]:
            q["group_by"].append("城市")
    elif "场所" in text or "餐饮街" in text or "商场" in text:
        if "经营场所" not in q["group_by"]:
            q["group_by"].append("经营场所")

    if "业态" in text or "渠道" in text:
        if "渠道组" not in q["group_by"]:
            q["group_by"].append("渠道组")

    if not q["group_by"]:
        q["group_by"] = ["规划大区" if "大区" in text else ("省份" if "省" in text else "渠道组")]

    q["group_by"] = list(dict.fromkeys(q["group_by"]))
    return q


# ----------------------------- C 类解析 -----------------------------
def parse_C(text: str, reg: FieldRegistry) -> dict:
    """C 类规则排行解析。"""
    cand_field = "经营场所名称"
    if "街道" in text or "街" in text:
        cand_field = "经营场所名称"
    elif "商圈" in text:
        cand_field = "商圈名称" if "商圈名称" in reg.fields else "经营场所名称"
    elif "商场" in text or "购物中心" in text:
        cand_field = "经营场所名称"
    elif "区县" in text or "区域" in text:
        cand_field = "区县"

    base_count = 20
    m = re.search(r"(?:不低于|大于|>=|≥)?\s*(\d+)\s*家", text)
    if m:
        base_count = int(m.group(1))

    rules = [
        {"name": "点评评分达标率", "field": "大众点评餐饮点评评分星级", "op": ">=", "value": 3.8, "weight": 0.5},
        {"name": "开业年限达标率", "field": "_derived:开店年限", "op": ">=", "value": 3, "weight": 0.5}
    ]

    return {
        "type": "C",
        "candidate_field": cand_field,
        "base_filter": [{"field": cand_field, "op": "not_null"}],
        "base_count": base_count,
        "rules": rules,
        "order_by": [{"field": "达标率", "dir": "desc"}]
    }


# ----------------------------- 第一棱镜：Analysis Intent 编译 -----------------------------
def compile_analysis_intent(text: str, reg: FieldRegistry, q: dict) -> dict:
    """第一棱镜：将自然语言提问与初步 DSL 编译为标准的本体落地 Analysis Intent。"""
    qtype = q.get("type", "A")
    intent_type = "detail_list" if qtype == "A" else ("distribution_comparison" if qtype == "B" else "rule_ranking")
    if "集中度" in text or "垄断" in text or "头部" in text:
        intent_type = "concentration_analysis"
    elif "背离" in text or "异动" in text or "冲突" in text:
        intent_type = "anomaly_detection"

    # 提取 scope
    scope = {}
    for f in q.get("filters", []):
        fld = f.get("field", "")
        if "省份" in fld:
            scope["province"] = f.get("value")
        elif "城市" in fld:
            scope["city"] = f.get("value")
        elif "规划大区" in fld:
            scope["territory"] = f.get("value")
        elif "渠道组" in fld:
            scope["channel"] = f.get("value")

    # 提取 entities 与 measures
    sem = _detect_semantic(text, reg)
    entities = sem.get("entities", [])
    measures = sem.get("measures", [])

    ambiguities = []
    for iso_name, iso_meta in reg.fields.items():
        if iso_meta.get("isolated") and iso_name in text:
            ambiguities.append(f"字段「{iso_name}」处于歧义隔离状态，未纳入执行计划")

    return {
        "question": text,
        "intent": intent_type,
        "target_entity": "prism:outlet/Outlet",
        "scope": scope,
        "entities": entities,
        "metrics": measures + [m.get("field") for m in q.get("metrics", []) if m.get("field")],
        "rules": q.get("rules", []),
        "target_quota": q.get("target_quota"),
        "priority_funnel": q.get("priority_funnel", []),
        "ambiguities": ambiguities,
        "requested_outputs": ["insight", "table"]
    }


# ----------------------------- 自动分类 -----------------------------
def auto_parse(text: str, reg: FieldRegistry) -> dict:
    """A/B/C 自动判断并编译第一棱镜 Analysis Intent。"""
    if any(k in text for k in ["占比", "为高", "为低", "为中", "达标", "30%", "50%"]) and "选" not in text:
        q = parse_C(text, reg)
    elif any(k in text for k in ["分布", "看看", "数量", "结构", "集中度", "垄断"]) and not any(k in text for k in ["选", "清单", "挑选", "圈选", "店清单", "门店清单"]):
        q = parse_B(text, reg)
    else:
        q = parse_A(text, reg)

    q["analysis_intent"] = compile_analysis_intent(text, reg, q)
    return q
