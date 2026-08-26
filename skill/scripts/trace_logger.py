# -*- coding: utf-8 -*-
"""
trace_logger.py
===============
链路 Trace 采集与可观测记录器 (Trace & Observability Logger v1.2)。
兼容 OpenTelemetry / Phoenix 标准格式，持久化记录查询全链路（提问 -> 解析 -> SQL/DSL -> 引擎执行 -> 审计 -> 洞察包）。

持久化文件：`output/traces.jsonl`
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_TRACE_LOG_PATH = PROJECT_ROOT / "output" / "traces.jsonl"


class TraceLogger:
    """结构化链路 Trace 记录器。"""

    def __init__(self, log_path: Path | str | None = None) -> None:
        self.log_path = Path(log_path or DEFAULT_TRACE_LOG_PATH)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log_trace(
        self,
        question: str,
        intent: dict[str, Any] | None = None,
        dsl: dict[str, Any] | None = None,
        sql: str | None = None,
        engine: str = "Vanna_DuckDB",
        rows_returned: int = 0,
        elapsed_seconds: float = 0.0,
        audit_report: dict[str, Any] | None = None,
        insight_package: dict[str, Any] | None = None,
        status: str = "success",
        error: str | None = None,
        trace_id: str | None = None
    ) -> dict[str, Any]:
        """记录单次完整执行链路并写入 JSONL。"""
        tid = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        timestamp = datetime.now(timezone.utc).isoformat()

        record = {
            "trace_id": tid,
            "timestamp": timestamp,
            "question": question,
            "status": status,
            "error": error,
            "execution": {
                "engine": engine,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "rows_returned": rows_returned,
                "sql": sql,
                "dsl": dsl,
            },
            "intent": intent,
            "audit": audit_report,
            "insight_summary": insight_package.get("conclusion", {}).get("summary") if insight_package else None,
            "confidence_level": insight_package.get("conclusion", {}).get("confidence_level") if insight_package else None,
            "limitations": insight_package.get("quality_and_limits", {}).get("limitations") if insight_package else [],
            "analysis_id": insight_package.get("analysis_id") if insight_package else None
        }

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return record

    def load_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        """读取最近的 Trace 记录。"""
        if not self.log_path.exists():
            return []
        traces = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        traces.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return traces[-limit:]


if __name__ == "__main__":
    logger = TraceLogger()
    t = logger.log_trace(
        question="测试链路追踪",
        sql="SELECT * FROM outlets LIMIT 10",
        engine="Vanna_DuckDB",
        rows_returned=10,
        elapsed_seconds=0.045,
        status="success"
    )
    print(f"[TraceLogger] 成功写入 Trace: {t['trace_id']}")
    traces = logger.load_traces(5)
    print(f"[TraceLogger] 最近记录数: {len(traces)}")
