# -*- coding: utf-8 -*-
"""
legacy_dataframe_api.py
=======================
外围兼容适配层：为历史脚本与现有测试提供基于 DataFrame 的调用接口，
将 DataFrame 转化为标准三元 InsightRequest 后调用纯粹的 Insight Component。
"""
from __future__ import annotations

from typing import Any
import pandas as pd
from adapters.dataframe_adapter import extract_insight_request
from insight import synthesize_from_request


def synthesize(dsl: dict | Any = None, result_df: pd.DataFrame | None = None,
               audit: dict | None = None, reg: Any = None,
               analysis_id: str | None = None,
               request: dict | None = None) -> dict[str, Any]:
    """向后兼容外围入口：接收 DataFrame/DSL 并通过适配器路由至纯 InsightSynthesizer。"""
    if request is not None:
        return synthesize_from_request(request, analysis_id)

    req = extract_insight_request(dsl, result_df, audit, request_id=analysis_id)
    return synthesize_from_request(req, analysis_id)
