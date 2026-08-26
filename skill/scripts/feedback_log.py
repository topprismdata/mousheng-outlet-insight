"""
feedback_log.py
===============
HIP 纠偏审计日志（对齐 PRD FR-37 / W3C PROV-DM 信任链）。

把 memory.py 的「纠偏飞轮」升级为**独立、结构化、可取证**的审计模块：

- 每次纠偏 / 确认 / 黑话解释都结构化为 PROV-DM 的 **Entity / Activity / Agent** 三元组（FR-37 AC1）
- 未审核匹配与高风险纠正强制携带 `match_method / match_score / review_status / risk_level` 元数据（FR-37 AC3）
- 可导出 **PROV-JSON** 包，供独立审计系统（provtoolbox / provpy）解析（FR-37 AC2）
- 与 §13.9.2 RO-Crate 复现包可互嵌（FR-37 AC4，通过 `to_prov` 输出的标准 bundle）

设计原则（与全项目一致）：
- **零 LLM、确定性**：所有字段由调用方显式传入或由状态推导，不做任何概率性"猜测真相"。
- **与 RAG 记忆解耦**：持久化到独立的 `references/feedback_log.jsonl`，不污染 `query_memory.jsonl`；
  memory.py 的 `correct/corrections` 仍作为飞轮捷径保留，本模块是**取证级审计底座**。

PROV-DM 映射见 PRD §14.6：
- Entity：待解析自然语言、原始候选 DSL、最终执行计划
- Activity：HIP 交互澄清、审核发布
- Agent：业务人员、系统智能体、口径审核负责人
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

FB_PATH = Path(__file__).parent.parent / "references" / "feedback_log.jsonl"

# ---- 审核状态 ----
REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"

# ---- 记录类型 ----
KIND_CORRECTION = "correction"
KIND_CONFIRMATION = "confirmation"
KIND_JARGON = "jargon_explanation"

# ---- 风险等级（高风险纠正必须 pending + 强制标注） ----
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"


def _now() -> float:
    return time.time()


def _rid() -> str:
    return "fb_" + uuid.uuid4().hex[:12]


class FeedbackLog:
    """HIP 纠偏审计日志（PROV-DM 结构化、可审核、可导出）。"""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else FB_PATH
        self.records: list[dict] = self._load()

    # ----------------------------- 加载 / 保存 -----------------------------
    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            for r in self.records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ----------------------------- 记录 -----------------------------
    def _make_record(self, rid, kind, nl, candidate_dsl, final_dsl,
                     match_method, match_score, business_person, system_agent,
                     risk_level, semantic_version) -> dict:
        # 审核状态默认 pending：高风险 / 模糊匹配 / 非精确匹配 一律进入待审，
        # 不允许概率性纠正静默当作事实真相（FR-37 AC3）。
        rec = {
            "id": rid,
            "kind": kind,
            "nl": nl,
            "candidate_dsl": candidate_dsl,
            "final_dsl": final_dsl,
            "match_method": match_method,
            "match_score": match_score,
            "review_status": REVIEW_PENDING,
            "risk_level": risk_level,
            "semantic_version": semantic_version,
            "agents": {
                "business_person": business_person,
                "system_agent": system_agent,
                "reviewer": None,
            },
            "ts": _now(),
        }
        return rec

    def log_correction(self, nl: str, final_dsl: dict,
                       candidate_dsl: dict | None = None,
                       match_method: str = "exact", match_score: float = 1.0,
                       business_person: str = "user", system_agent: str = "nl_parser",
                       risk_level: str = RISK_LOW,
                       semantic_version: str = "unknown") -> str:
        """记录一次纠偏（用户纠正了系统的解析结果）。返回记录 id。"""
        rid = _rid()
        rec = self._make_record(rid, KIND_CORRECTION, nl, candidate_dsl, final_dsl,
                                match_method, match_score, business_person,
                                system_agent, risk_level, semantic_version)
        self.records.append(rec)
        self._save()
        return rid

    def log_confirmation(self, nl: str, final_dsl: dict,
                         business_person: str = "user", system_agent: str = "nl_parser",
                         risk_level: str = RISK_LOW,
                         semantic_version: str = "unknown") -> str:
        """记录一次确认（用户认可系统解析）。"""
        rid = _rid()
        rec = self._make_record(rid, KIND_CONFIRMATION, nl, None, final_dsl,
                                "exact", 1.0, business_person, system_agent,
                                risk_level, semantic_version)
        self.records.append(rec)
        self._save()
        return rid

    def log_jargon(self, nl: str, explanation: str, final_dsl: dict | None = None,
                   business_person: str = "user", system_agent: str = "nl_parser",
                   risk_level: str = RISK_MEDIUM,
                   semantic_version: str = "unknown") -> str:
        """记录一次未知黑话的澄清解释（默认中风险，需审核）。"""
        rid = _rid()
        rec = self._make_record(rid, KIND_JARGON, nl, None, final_dsl,
                                "exact", 1.0, business_person, system_agent,
                                risk_level, semantic_version)
        rec["explanation"] = explanation
        self.records.append(rec)
        self._save()
        return rid

    # ----------------------------- 检索 -----------------------------
    def get(self, rid: str) -> dict | None:
        for r in self.records:
            if r["id"] == rid:
                return r
        return None

    def pending(self) -> list[dict]:
        """返回所有待审核记录（未审核匹配 / 高风险纠正必在其中）。"""
        return [r for r in self.records if r["review_status"] == REVIEW_PENDING]

    # ----------------------------- 审核 -----------------------------
    def review(self, rid: str, decision: str, reviewer: str) -> dict:
        """审核一条记录：decision ∈ {approved, rejected}。返回更新后的记录。"""
        rec = self.get(rid)
        if rec is None:
            raise KeyError(f"no feedback record {rid}")
        if decision not in (REVIEW_APPROVED, REVIEW_REJECTED):
            raise ValueError(f"decision must be '{REVIEW_APPROVED}' or '{REVIEW_REJECTED}'")
        rec["review_status"] = decision
        rec["agents"]["reviewer"] = reviewer
        rec["reviewed_at"] = _now()
        self._save()
        return rec

    # ----------------------------- PROV 导出（FR-37 AC1/AC2/AC4） -----------------------------
    def to_prov(self, rid: str) -> dict:
        """导出单条记录为 W3C PROV-JSON bundle（实体/活动/代理三元组）。

        输出为合法 PROV-JSON 结构，可被 provtoolbox/provpy 解析校验（AC2）；
        亦可作为 §13.9.2 RO-Crate 的 provenance 节点嵌入（AC4）。
        """
        rec = self.get(rid)
        if rec is None:
            raise KeyError(f"no feedback record {rid}")

        rid_uri = f"urn:feedback:{rid}"
        nl_uri = f"{rid_uri}:nl"
        cand_uri = f"{rid_uri}:candidate_dsl"
        final_uri = f"{rid_uri}:final_dsl"
        act_uri = f"{rid_uri}:hip_activity"
        bp_uri = f"urn:agent:{rec['agents']['business_person']}"
        sa_uri = f"urn:agent:{rec['agents']['system_agent']}"
        rv_uri = f"urn:agent:{rec['agents']['reviewer'] or 'unreviewed'}"

        entity: dict = {
            nl_uri: {"prov:value": rec["nl"], "prov:type": "fb:NaturalLanguage"},
            final_uri: {
                "prov:value": json.dumps(rec["final_dsl"], ensure_ascii=False),
                "prov:type": "fb:FinalPlan",
            },
        }
        if rec.get("candidate_dsl") is not None:
            entity[cand_uri] = {
                "prov:value": json.dumps(rec["candidate_dsl"], ensure_ascii=False),
                "prov:type": "fb:CandidateDSL",
            }

        bundle = {
            "prefix": {
                "xsd": "http://www.w3.org/2001/XMLSchema#",
                "prov": "http://www.w3.org/ns/prov#",
                "fb": "urn:feedback:",
            },
            "entity": entity,
            "activity": {
                act_uri: {
                    "prov:type": "fb:HIPClarification",
                    "prov:startTime": rec["ts"],
                    "fb:kind": rec["kind"],
                    "fb:match_method": rec["match_method"],
                    "fb:match_score": rec["match_score"],
                    "fb:risk_level": rec["risk_level"],
                    "fb:review_status": rec["review_status"],
                    "fb:semantic_version": rec["semantic_version"],
                },
            },
            "agent": {
                bp_uri: {"prov:type": "prov:Person", "fb:role": "business_person"},
                sa_uri: {"prov:type": "prov:SoftwareAgent", "fb:role": "system_agent"},
                rv_uri: {"prov:type": "prov:Person", "fb:role": "reviewer"},
            },
            "wasGeneratedBy": {
                f"{final_uri}-gen": {"prov:entity": final_uri, "prov:activity": act_uri},
            },
            "wasAttributedTo": {
                f"{final_uri}-attr": {"prov:entity": final_uri, "prov:agent": bp_uri},
            },
            "wasAssociatedWith": {
                f"{act_uri}-assoc-bp": {"prov:activity": act_uri, "prov:agent": bp_uri},
                f"{act_uri}-assoc-sa": {"prov:activity": act_uri, "prov:agent": sa_uri},
            },
        }
        if rec["agents"]["reviewer"]:
            bundle["wasAssociatedWith"][f"{act_uri}-assoc-rv"] = {
                "prov:activity": act_uri, "prov:agent": rv_uri,
            }
        return bundle

    def clear(self) -> None:
        self.records = []
        if self.path.exists():
            self.path.unlink()


if __name__ == "__main__":
    # 自测：一条高风险纠偏 → 待审 → 导出 PROV → 审核通过
    fl = FeedbackLog(Path(__file__).parent.parent / "references" / "_selftest_feedback_log.jsonl")
    rid = fl.log_correction(
        nl="列出营业到晚上10点后的火锅店",
        final_dsl={"type": "A", "filters": [{"field": "大众点评营业时间", "op": "close_after", "value": 22}]},
        candidate_dsl={"type": "A", "filters": []},
        match_method="fuzzy", match_score=0.78,
        business_person="张经理", system_agent="nl_parser",
        risk_level=RISK_HIGH, semantic_version="field_registry@v3",
    )
    print("logged:", rid, "pending:", len(fl.pending()))
    prov = fl.to_prov(rid)
    print("prov keys:", sorted(prov.keys()))
    fl.review(rid, REVIEW_APPROVED, "李审核")
    print("after review:", fl.get(rid)["review_status"], fl.get(rid)["agents"]["reviewer"])
    fl.clear()
    print("self-test OK")
