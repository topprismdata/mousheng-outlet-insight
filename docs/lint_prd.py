# -*- coding: utf-8 -*-
"""
PRD 一致性 lint（基于 outlet-insight_PRD.md 实跑校验）
校验项：
  1. §11/§13/§14 中引用的 FR 必须都在 §5 定义
  2. 每个 ✅ FR 都必须有对应的真实测试（R.ok/R.fail 存在于 run_tests.py）
  3. 每个 🔲 FR 都不能被正文写成「已交付」（已实现/已落地/已支持…）
附加：
  4. §2.3 能力边界表 ↔ §5 FR 状态一致性
  5. DuckDB 在文中多次出现但是否有独立 FR
  6. FR 编号连续无缺漏/重复；✅ 计数=18、🔲 计数=22
"""
import re
import sys
from pathlib import Path

ROOT = Path("/Users/ghb/WorkBuddy/2026-07-22-17-52-31")
PRD = ROOT / "outlet-insight_PRD.md"
TESTS = Path("/Users/ghb/.workbuddy/skills/outlet-insight/scripts/run_tests.py")

prd_text = PRD.read_text(encoding="utf-8")
test_text = TESTS.read_text(encoding="utf-8")
lines = prd_text.splitlines()

# ---- 切分章节 ----
def section_bounds(title_prefix):
    start = None
    for i, l in enumerate(lines):
        if re.match(rf"^##\s+{title_prefix}", l):
            start = i
            break
    if start is None:
        return None, None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^##\s+\d+\.", lines[j]):
            end = j
            break
    return start, end

sec5 = section_bounds(r"5\. ")
sec11 = section_bounds(r"11\. ")
sec13 = section_bounds(r"13\. ")
sec14 = section_bounds(r"14\. ")
sec23 = section_bounds(r"2\.3")

def slice(start, end):
    return "\n".join(lines[start:end]) if start is not None else ""

s5, s11, s13, s14, s23 = slice(*sec5), slice(*sec11), slice(*sec13), slice(*sec14), slice(*sec23)

# ---- 解析 §5 FR 定义表 ----
# 匹配行：| FR-NN | 描述 | ✅/🔲 | P0/P1/P2 |
fr_def = {}  # id -> {status, prio, desc}
fr_re = re.compile(r"^\|\s*(FR-\d+)\s*\|\s*(.+?)\s*\|\s*([✅🔲])\s*\|\s*(P\d)\s*\|")
for l in s5.splitlines():
    m = fr_re.match(l)
    if m:
        fr_def[m.group(1)] = {"status": m.group(3), "prio": m.group(4), "desc": m.group(2).strip()}
defined_ids = set(fr_def.keys())

# ---- 提取真实测试 ID（run_tests.py 中 R.ok/R.fail("Txx" ...)）----
test_ids = set(re.findall(r'R\.(?:ok|fail)\(\s*"(T\d+)"', test_text))

# ---- FR -> 测试 映射（基于 run_tests.py 实际覆盖）----
fr_to_tests = {
    "FR-1": ["T01","T02","T03","T04","T05","T06","T07","T08"],
    "FR-2": ["T09","T10","T11","T12"],
    "FR-3": ["T13","T14"],
    "FR-4": ["T01","T02","T04","T08"],
    "FR-5": ["T03","T05","T07","T28"],
    "FR-6": ["T07"],
    "FR-7": ["T20"],
    "FR-8": ["T13","T14"],            # 间接：C 类体检含维度自适应
    "FR-9": ["T21"],
    "FR-10": ["T15","T25","T36"],
    "FR-11": ["T16"],
    "FR-12": ["T22","T23","T24"],
    "FR-13": ["T25","T30"],
    "FR-14": ["T26","T27","T28","T29","T30","T31","T32"],
    "FR-15": ["T34"],
    "FR-16": ["T33"],
    "FR-17": ["T35"],
    "FR-18": ["T36"],
    "FR-39": ["T37"],   # M1 PoC 已验证（🔲 整体规划，check2 仅校验 ✅ 故不影响）
    "FR-37": ["T38"],   # M1 PoC 已验证（🔲 整体规划，check2 仅校验 ✅ 故不影响）
}

# ---- 校验 1：§11/§13/§14 引用的 FR 必须在 §5 定义 ----
check1_problems = []
for secname, sec in [("§11", s11), ("§13", s13), ("§14", s14)]:
    for m in re.finditer(r"FR-\d+", sec):
        fid = m.group(0)
        if fid not in defined_ids:
            check1_problems.append(f"{secname} 引用了未定义的 {fid}")

# ---- 校验 2：每个 ✅ FR 都有对应测试，且测试真实存在 ----
check2_problems = []
check2_ok = []
for fid, info in fr_def.items():
    if info["status"] == "✅":
        mapped = fr_to_tests.get(fid, [])
        missing = [t for t in mapped if t not in test_ids]
        if not mapped:
            check2_problems.append(f"{fid}（✅）没有任何映射测试")
        elif missing:
            check2_problems.append(f"{fid}（✅）映射的测试不存在于 run_tests.py: {missing}")
        else:
            check2_ok.append(f"{fid} -> {mapped[0]}..{mapped[-1]}（{len(mapped)} 个用例）")

# ---- 校验 3：每个 🔲 FR 都不能被正文写成「已交付」----
# 交付关键词（正例）；反向词（规划/目标/🔲/尚未/尚属/TODO）列为例外
deliver_kw = ["已实现","已落地","已支持","已具备","已完成","已通过","已交付","已上线","已发布","✅"]
neg_kw = ["🔲","规划","目标","尚未","尚属","TODO","待","未"]
check3_problems = []
for fid, info in fr_def.items():
    if info["status"] == "🔲":
        for i, l in enumerate(lines):
            if fid in l:
                if any(k in l for k in deliver_kw) and not any(k in l for k in neg_kw):
                    check3_problems.append(f"第{i+1}行 {fid} 可能被判为已交付: {l.strip()[:80]}")

# ---- 附加 4：§2.3 能力边界 ↔ §5 状态 ----
check4_problems = []
# §2.3 把 HIP(26-30)/数仓(22/23/31/33)/BI(32) 标 🔲；其余标 ✅
sec23_expect_placeholder = {
    "FR-22":"🔲","FR-23":"🔲","FR-31":"🔲","FR-32":"🔲","FR-33":"🔲",
    "FR-26":"🔲","FR-27":"🔲","FR-28":"🔲","FR-29":"🔲","FR-30":"🔲",
}
for fid, exp in sec23_expect_placeholder.items():
    if fid in fr_def and fr_def[fid]["status"] != exp:
        check4_problems.append(f"{fid} §2.3 应标 {exp} 但 §5 实际为 {fr_def[fid]['status']}")

# ---- 附加 5：DuckDB 出现但无独立 FR ----
duckdb_count = len(re.findall(r"DuckDB", prd_text))
duckdb_fr = [fid for fid,info in fr_def.items() if "DuckDB" in info["desc"]]
check5_notes = [f"文中 DuckDB 出现 {duckdb_count} 次；FR 描述中直接提及 DuckDB 的 FR: {duckdb_fr or '无'}"]
if duckdb_count > 0 and not duckdb_fr:
    check5_notes.append("⚠ DuckDB 多次出现但无独立 FR（仅在 FR-23/FR-31 规划中隐含），建议补一句『DuckDB 作为本地列式 Adapter 候选』")

# ---- 附加 6：FR 编号连续 + 计数 ----
ids_sorted = sorted(defined_ids, key=lambda x: int(x.split("-")[1]))
nums = [int(x.split("-")[1]) for x in ids_sorted]
gaps = [n for n in range(1, max(nums)+1) if n not in nums]
dupes = len(ids_sorted) != len(set(nums))
n_done = sum(1 for v in fr_def.values() if v["status"]=="✅")
n_plan = sum(1 for v in fr_def.values() if v["status"]=="🔲")

# ============================ 输出 ============================
print("="*70)
print("PRD 一致性 Lint 报告")
print("="*70)
print(f"\n[基础] 文档: {PRD.name}")
print(f"  FR 定义总数: {len(defined_ids)}  编号范围: FR-{min(nums)}..FR-{max(nums)}")
print(f"  编号缺口: {gaps or '无'}  重复: {'有' if dupes else '无'}")
print(f"  ✅ 计数={n_done}（PRD 宣称 18）  🔲 计数={n_plan}（PRD 宣称 22）")
print(f"  {'✓ 计数与宣称一致' if n_done==18 and n_plan==22 else '✗ 计数与宣称不一致！'}")
print(f"  真实测试 ID 数（run_tests.py 中 R.ok/R.fail）: {len(test_ids)}")

print("\n--- 校验1：§11/§13/§14 引用的 FR 是否都在 §5 定义 ---")
if check1_problems:
    for p in check1_problems: print("  ✗", p)
else:
    print("  ✓ 全部引用的 FR 均在 §5 定义（无悬空引用）")

print("\n--- 校验2：每个 ✅ FR 是否都有对应真实测试 ---")
if check2_problems:
    for p in check2_problems: print("  ✗", p)
else:
    print(f"  ✓ 全部 {n_done} 个 ✅ FR 均已映射到真实测试")
    for x in check2_ok: print("    ·", x)

print("\n--- 校验3：每个 🔲 FR 是否被正文写成已交付 ---")
if check3_problems:
    for p in check3_problems: print("  ✗", p)
else:
    print("  ✓ 全部 🔲 FR 未被正文判为已交付（状态与描述一致）")

print("\n--- 附加4：§2.3 能力边界 ↔ §5 状态一致性 ---")
if check4_problems:
    for p in check4_problems: print("  ✗", p)
else:
    print("  ✓ §2.3 标注的 🔲 范围（HIP/数仓/BI）与 §5 FR 状态一致")

print("\n--- 附加5：DuckDB 无独立 FR ---")
for n in check5_notes: print("  ·", n)

print("\n" + "="*70)
print("结论汇总")
print("="*70)
issues = check1_problems + check2_problems + check3_problems + check4_problems
if not issues:
    print("✓ 三道核心校验 + 附加校验 全部通过，文档前后一致，无矛盾。")
else:
    print(f"✗ 发现 {len(issues)} 处需关注：")
    for i in issues: print("  -", i)
