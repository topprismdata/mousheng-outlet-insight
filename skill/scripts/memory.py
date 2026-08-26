"""
memory.py
=========
RAG 黄金查询记忆（借鉴 Vanna 的「历史黄金查询 + 纠偏日志」自学飞轮）。

把成功执行的 (自然语言 → DSL → 结果摘要) 与用户显式纠偏持久化到本地
references/query_memory.jsonl；下次遇到相似问题时优先复用，让 agent 越用越准。

相似度：默认用本地中文 embedding 模型（fastembed + BAAI/bge-small-zh-v1.5，
余弦相似度），对「近义 / 语序不同」问法召回远优于字符级 bigram。
若 fastembed 不可用（未安装 / 模型加载失败），自动降级为 bigram Jaccard，
保证 Skill 在任何环境都能跑（仅召回质量下降，不报错）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np

MEM_PATH = Path(__file__).parent.parent / "references" / "query_memory.jsonl"
EMB_PATH = Path(__file__).parent.parent / "references" / "query_memory_emb.npz"
MAX_RECORDS = 300

EMB_MODEL = "BAAI/bge-small-zh-v1.5"


class Embedder:
    """轻量本地中文 embedding 封装（fastembed）。不可用时 available=False。"""

    def __init__(self) -> None:
        self.model = None
        self.dim = 0
        self.error: Exception | None = None
        try:
            from fastembed import TextEmbedding
            self.model = TextEmbedding(model_name=EMB_MODEL)
            # 探测维度
            v = self.model.embed(["探测"]).__iter__().__next__()
            self.dim = int(np.asarray(v).shape[-1])
        except Exception as e:  # 未安装 / 模型下载失败 / 其他
            self.model = None
            self.error = e

    @property
    def available(self) -> bool:
        return self.model is not None

    def encode(self, texts: list[str]) -> np.ndarray:
        """返回 L2 归一化后的向量矩阵 (N, dim)。"""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vecs = [np.asarray(v, dtype=np.float32) for v in self.model.embed(list(texts))]
        vecs = np.stack(vecs)
        n = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = np.where(n > 0, vecs / n, vecs)
        return vecs


EMB = Embedder()


# ----------------------------- 字符级兜底 -----------------------------
def _tokens(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text)
    if len(text) <= 1:
        return {text}
    return set(text[i:i + 2] for i in range(len(text) - 1)) | {text}


def _sim(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class QueryMemory:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else MEM_PATH
        self.emb_path = self.path.parent / "query_memory_emb.npz"
        self.records: list[dict] = self._load()
        # 向量缓存：与 records 同序；available 时为 list[ndarray]，否则 None
        self._embs = self._load_emb() if EMB.available else None

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

    def _load_emb(self) -> list[np.ndarray] | None:
        if not EMB.available:
            return None
        if self.emb_path.exists() and self.records:
            try:
                arr = np.load(self.emb_path)["embs"]
                if arr.shape[0] == len(self.records):
                    return [arr[i] for i in range(arr.shape[0])]
            except Exception:
                pass
        # 缺失 / 不对齐 → 全量重算（300 条内 <1s）
        self._recompute_embs()
        return self._embs

    def _recompute_embs(self) -> None:
        if not EMB.available:
            self._embs = None
            return
        if not self.records:
            self._embs = []
            return
        self._embs = list(EMB.encode([r["nl"] for r in self.records]))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            for r in self.records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if EMB.available and self._embs is not None and self._embs:
            try:
                np.savez(self.emb_path, embs=np.stack(self._embs))
            except Exception:
                pass

    # ----------------------------- 写入 -----------------------------
    def record(self, nl: str, dsl: dict, rows: int, cols: int, scope: str) -> None:
        # 去重：同 nl 的成功记录只保留最新一条 —— 避免重复累积，
        # 也为「纠偏可废止」提供基础（用户正常再问一次即可覆盖旧纠偏）。
        self.records = [r for r in self.records
                        if not (r.get("nl") == nl and r.get("kind") == "success")]
        self.records.append({
            "nl": nl, "dsl": dsl, "rows": rows, "cols": cols,
            "scope": scope, "kind": "success", "ts": time.time(),
        })
        if EMB.available:
            self._embs.append(EMB.encode([nl])[0])
        self.records = self.records[-MAX_RECORDS:]
        if EMB.available and self._embs is not None:
            self._embs = self._embs[-MAX_RECORDS:]
        self._save()

    def correct(self, nl: str, correct_dsl: dict) -> None:
        """记录用户对解析结果的纠偏（nl → 正确 DSL）。

        同 nl 的旧纠偏会被替换（而非追加），避免纠偏无限累积与永久锁定。
        """
        self.records = [r for r in self.records
                        if not (r.get("nl") == nl and r.get("kind") == "correction")]
        self.records.append({
            "nl": nl, "dsl": correct_dsl, "rows": -1, "cols": -1,
            "scope": "correct", "kind": "correction", "ts": time.time(),
        })
        if EMB.available:
            self._embs.append(EMB.encode([nl])[0])
        self.records = self.records[-MAX_RECORDS:]
        if EMB.available and self._embs is not None:
            self._embs = self._embs[-MAX_RECORDS:]
        self._save()

    def clear(self) -> None:
        self.records = []
        self._embs = [] if EMB.available else None
        if self.path.exists():
            self.path.unlink()
        if self.emb_path.exists():
            self.emb_path.unlink()

    # ----------------------------- 相似度评分 -----------------------------
    def _scored(self, nl: str, kind: str | None) -> list[tuple[float, dict]]:
        """返回 [(score, rec)]，按 score 降序。向量可用时用余弦，否则用 bigram。"""
        cands = [(i, r) for i, r in enumerate(self.records)
                 if kind is None or r.get("kind") == kind]
        if EMB.available and self._embs is not None:
            qv = EMB.encode([nl])[0]
            out = []
            for i, r in cands:
                e = self._embs[i] if i < len(self._embs) and self._embs[i] is not None else None
                if e is not None:
                    out.append((float(np.dot(qv, e)), r))
                else:
                    out.append((_sim(nl, r["nl"]), r))
            out.sort(key=lambda x: -x[0])
            return out
        return sorted([(_sim(nl, r["nl"]), r) for _, r in cands], key=lambda x: -x[0])

    # ----------------------------- 检索 -----------------------------
    def recall(self, nl: str, k: int = 3, threshold: float = 0.30) -> list[dict]:
        thr = max(threshold, 0.50) if EMB.available else threshold
        return [r for s, r in self._scored(nl, "success")[:k] if s >= thr]

    def corrections(self, nl: str, k: int = 2, threshold: float = 0.35) -> list[dict]:
        thr = max(threshold, 0.60) if EMB.available else threshold
        return [r for s, r in self._scored(nl, "correction")[:k] if s >= thr]

    def best_match_dsl(self, nl: str, high: float = 0.85) -> dict | None:
        """高相似度时直接复用历史 DSL（跳过重新解析，确保一致）。"""
        thr = max(high, 0.82) if EMB.available else high
        hits = self._scored(nl, "success")
        return hits[0][1]["dsl"] if hits and hits[0][0] >= thr else None
