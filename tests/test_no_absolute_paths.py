# -*- coding: utf-8 -*-
"""
回归门禁（issue #2）：git 跟踪的文件中禁止出现硬编码的绝对用户路径。

覆盖三类前缀：macOS 用户主目录、Linux 用户主目录、Windows 用户目录
（本文件刻意用字符串拼接构造匹配串，避免自我命中）。
命令示例一律写 `python3`；文件路径用仓库相对形式或环境变量
（数据定位：`OUTLET_DATA`；上游 prism-ontology dist：将引入 `PRISM_DIST_DIR`）。

实现方式：对 git 跟踪的工作区文件执行 `git grep`（-I 跳过二进制、-F 固定串），
因此未跟踪的 data/ 与 output/ 目录天然不在检查范围内。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# 匹配串通过拼接构造，避免本测试文件自身的源码命中被检前缀。
_BS = chr(92)  # 反斜杠
PATTERNS = (
    "/" + "Users/",              # macOS 用户主目录前缀
    "/" + "home/",               # Linux 用户主目录前缀
    "C:" + _BS + "Users" + _BS,  # Windows 用户目录前缀
)

# 豁免清单（git 路径，当前为空）。豁免必须在紧邻注释中给出理由
# （为什么必须含该字面量、为何无法用占位符或环境变量替代）与评审出处
# （issue/PR 链接），例：
#   ALLOWLIST = {"docs/cross-platform-notes.md"}  # PR #123: 需展示真实 Windows 路径示例
ALLOWLIST = set()


def _tracked_violations():
    """在工作区 git 跟踪文件中检索硬编码绝对用户路径，返回违规行列表。"""
    if shutil.which("git") is None:
        pytest.skip("git 不可用，跳过硬编码路径检查")
    cmd = ["git", "-c", "core.quotepath=off", "grep", "-I", "-n", "-F"]
    for pat in PATTERNS:
        cmd += ["-e", pat]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        pytest.fail(f"git grep 执行失败（exit={proc.returncode}）：{proc.stderr.strip()}")
    violations = []
    for line in proc.stdout.splitlines():
        rel_path = line.split(":", 1)[0]
        if rel_path in ALLOWLIST:
            continue
        violations.append(line)
    return violations


def test_no_absolute_user_paths():
    """git 跟踪的文件不得包含硬编码绝对用户路径（issue #2 回归门禁）。"""
    violations = _tracked_violations()
    if violations:
        listing = "\n".join(f"  {v}" for v in violations)
        pytest.fail(
            f"发现 {len(violations)} 处硬编码绝对用户路径"
            f"（豁免清单见 tests/test_no_absolute_paths.py 的 ALLOWLIST）：\n{listing}"
        )
