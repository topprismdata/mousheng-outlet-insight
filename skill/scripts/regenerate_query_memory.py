#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regenerate_query_memory.py
==========================
重建/校验 skill/references/query_memory_emb.npz（RAG 黄金查询记忆的 embedding 向量缓存）。

定位（资产治理，见 docs/asset-governance.md）：query_memory.jsonl 与 query_memory_emb.npz
是运行时自学飞轮缓存，不入库。jsonl 由 skill/scripts/memory.py（QueryMemory.record/correct，
成功查询与纠偏）与 skill/scripts/feedback_integration.py（FailureFeedbackRecorder，拒答回流）
在运行时写入，内容含针对私有数据集的执行产物 —— 它不由提交输入再生，而是随使用重新积累。

本脚本不生成、不虚构任何记忆内容：只对磁盘上已有的 query_memory.jsonl 重新执行
memory.py 自身的写入路径（QueryMemory._recompute_embs + _save），重建与记录数对齐的
npz 向量缓存。npz 缺失/不对齐时 QueryMemory 本就会在下次加载时全量重算
（memory.py:102-114），本脚本只是把这一步显式化、可校验化。

用法：
    python regenerate_query_memory.py             # 校验，缺失/不对齐时重建 npz
    python regenerate_query_memory.py --check     # 只校验，不写盘（不对齐时退出码 1）
    python regenerate_query_memory.py --mem PATH  # 指定其他记忆文件

注意：npz 仅在 fastembed（BAAI/bge-small-zh-v1.5）可用时生成；不可用时 Skill 运行时
自动降级 bigram Jaccard 相似度（见 memory.py 模块注释），此时本脚本只校验 jsonl 并如实报告。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import numpy as np  # noqa: E402

from memory import EMB, EMB_MODEL, MEM_PATH, QueryMemory  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="重建/校验 query_memory_emb.npz 向量缓存（不改动记忆内容）")
    ap.add_argument("--mem", type=Path, default=MEM_PATH,
                    help="query_memory.jsonl 路径（默认 skill/references/query_memory.jsonl）")
    ap.add_argument("--check", action="store_true", help="只校验，不重建（不对齐时退出码 1）")
    args = ap.parse_args()

    mem = QueryMemory(args.mem)
    n = len(mem.records)
    print(f"[load] {args.mem}: {n} 条记录")

    if n == 0:
        print("[ok  ] 记忆为空（新装/已清空状态），无需 npz；运行时将随使用重新积累。")
        return 0

    aligned = False
    try:
        arr = np.load(mem.emb_path)["embs"]
        aligned = arr.shape[0] == n
        print(f"[npz ] {mem.emb_path}: shape={arr.shape} 与记录对齐={aligned}")
    except Exception as e:
        print(f"[npz ] {mem.emb_path}: 缺失或不可读 ({e})")

    if not EMB.available:
        print("[emb ] fastembed 不可用 → 运行时自动降级 bigram Jaccard，无需 npz。仅完成 jsonl 校验。")
        return 0
    if aligned:
        print(f"[ok  ] 向量缓存已对齐（model={EMB_MODEL}），无需重建。")
        return 0
    if args.check:
        print("[check] npz 缺失/不对齐（--check 只读，未重建）。")
        return 1

    mem._recompute_embs()
    mem._save()  # memory.py 的唯一 npz 写入路径（memory.py:125-134）
    arr = np.load(mem.emb_path)["embs"]
    ok = arr.shape[0] == n
    print(f"[save] 重建完成: shape={arr.shape}, model={EMB_MODEL}, 对齐={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
