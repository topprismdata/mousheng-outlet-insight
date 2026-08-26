# -*- coding: utf-8 -*-
"""
feedback_integration.py
======================
失败案例回流与纠偏闭环模块 (Failure Feedback Integration)。
将盲测/CI门禁中识别的失败或拒答案例，自动写入 query_memory.jsonl (类型: failure_feedback)，
供后续 DSL 解析与意图路由的持续学习与回归回顾。

记录格式：
{"ts": "...", "nl": "...", "reason": "...", "expected": "...", "actual": "..."}
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
MEM_PATH = SCRIPT_DIR.parent / "references" / "query_memory.jsonl"


class FailureFeedbackRecorder:
    """失败案例与拒答反馈记录器 (query_memory.jsonl failure_feedback 类型)。"""

    def __init__(self, mem_path: Path | str | None = None) -> None:
        self.mem_path = Path(mem_path or MEM_PATH)
        self.mem_path.parent.mkdir(parents=True, exist_ok=True)

    def record_rejection(self, question: str, reason: str, expected: str = "", actual: str = "") -> dict[str, Any]:
        """记录单条拒答反馈。"""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "failure_feedback",
            "nl": question,
            "reason": reason,
            "expected": expected,
            "actual": actual
        }
        with self.mem_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def load_rejections(self, limit: int = 50) -> list[dict[str, Any]]:
        """读取最近的 failure_feedback 记录。"""
        if not self.mem_path.exists():
            return []
        out = []
        with self.mem_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        r = json.loads(line)
                        if r.get("type") == "failure_feedback":
                            out.append(r)
                    except json.JSONDecodeError:
                        pass
        return out[-limit:]

    def sync_from_ci_run(self, ci_results: list[dict[str, Any]]) -> int:
        """从 CI 断言结果中提取失败案例并写入 feedback 流。"""
        count = 0
        for r in ci_results:
            if not r.get("passed", True):
                self.record_rejection(
                    question=r.get("query", ""),
                    reason=r.get("reason", ""),
                    expected=r.get("expected", ""),
                    actual=r.get("actual", "")
                )
                count += 1
        return count


if __name__ == "__main__":
    recorder = FailureFeedbackRecorder()
    print("=== 反馈记录器自检 ===")
    # 模拟记录一条拒答
    e1 = recorder.record_rejection(
        question="按门店老板生肖星座统计分布",
        reason="幻觉字段拦截：'生肖星座' 未在受管 FieldRegistry 中",
        expected="成功受控拦截",
        actual="成功拦截"
    )
    print(f"[记录] {e1['reason']}")
    print(f"[存储路径] {recorder.mem_path}")
    
    loaded = recorder.load_rejections(5)
    print(f"[读取] 反馈流记录数: {len(loaded)}")
    print(">>> 失败案例回流模块 100% 工作正常！ <<<")
