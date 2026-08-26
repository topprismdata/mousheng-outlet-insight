# -*- coding: utf-8 -*-
"""
test_doc_examples.py
====================
专门测试并保证 examples/ 目录中的权威示例 JSON 与 SKILL.md / PRD 文档引用 100% 真实可执行，
杜绝文档与代码 Schema、输入与输出配对之间产生任何微小漂移。

权威源：
- examples/insight-request.example.json  -> InsightRequest 输入契约示例
- examples/insight-package.example.json -> InsightPackage 输出契约示例 (由 request 确定性生成)
"""
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from insight import (
    synthesize_from_request,
    validate_insight_request,
    validate_insight_package
)

# SKILL_DIR = /Users/ghb/outlet-insight/skill
SKILL_DIR = Path(__file__).resolve().parent.parent
# Project ROOT = /Users/ghb/outlet-insight
PROJECT_ROOT = SKILL_DIR.parent
EXAMPLES_DIR = PROJECT_ROOT / "examples"
REQ_EXAMPLE_PATH = EXAMPLES_DIR / "insight-request.example.json"
PKG_EXAMPLE_PATH = EXAMPLES_DIR / "insight-package.example.json"
REQ_SCHEMA_PATH = SKILL_DIR / "references" / "insight-request.schema.json"
PKG_SCHEMA_PATH = SKILL_DIR / "references" / "insight-package.schema.json"

with open(REQ_SCHEMA_PATH, "r", encoding="utf-8") as f:
    REQ_SCHEMA = json.load(f)
with open(PKG_SCHEMA_PATH, "r", encoding="utf-8") as f:
    PKG_SCHEMA = json.load(f)

REQ_VALIDATOR = Draft202012Validator(REQ_SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)
PKG_VALIDATOR = Draft202012Validator(PKG_SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_examples_dir_contains_authority_json():
    """examples 目录必须包含唯一权威示例文件。"""
    assert REQ_EXAMPLE_PATH.exists(), f"缺少 InsightRequest 权威示例: {REQ_EXAMPLE_PATH}"
    assert PKG_EXAMPLE_PATH.exists(), f"缺少 InsightPackage 权威示例: {PKG_EXAMPLE_PATH}"
    print("  ✓ examples 目录包含唯一权威示例文件: PASSED")


def test_authority_request_and_package_exact_pairing():
    """验证 examples/insight-package.example.json 是 examples/insight-request.example.json 运行的精确配对结果。"""
    with open(REQ_EXAMPLE_PATH, "r", encoding="utf-8") as f:
        req_example = json.load(f)
    with open(PKG_EXAMPLE_PATH, "r", encoding="utf-8") as f:
        pkg_example = json.load(f)

    # 1. 校验输入 Schema
    validate_insight_request(req_example)

    # 2. 校验输出 Schema
    validate_insight_package(pkg_example)

    # 3. 严格配对执行与断言：真实合成结果必须与输出示例完全相等 (包括数值、结构与 provenance)
    actual_pkg = synthesize_from_request(
        req_example,
        analysis_id=pkg_example["analysis_id"],
        generated_at=pkg_example["provenance"]["insight_generated_at"]
    )

    assert actual_pkg == pkg_example, "权威输入示例与输出示例未精确配对（合成结果与输出示例不一致）！"
    assert pkg_example["provenance"]["insight_component_version"] == "1.3.0-rc3"
    assert pkg_example["structure"]["concentration_metrics"]["hhi"] == 2850.1
    print("  ✓ examples 权威输入与输出示例 100% 精确配对与 Schema 校验: PASSED")


def test_idempotency_with_injected_timestamps():
    """传入完全相同的 analysis_id 与 generated_at 必须产生字节级完全一致的 InsightPackage。"""
    with open(REQ_EXAMPLE_PATH, "r", encoding="utf-8") as f:
        req_example = json.load(f)

    pkg1 = synthesize_from_request(
        req_example,
        analysis_id="fixed-test-id",
        generated_at="2026-08-25T12:00:00Z"
    )
    pkg2 = synthesize_from_request(
        req_example,
        analysis_id="fixed-test-id",
        generated_at="2026-08-25T12:00:00Z"
    )
    
    pkg1_norm = json.dumps(pkg1, ensure_ascii=False, sort_keys=True)
    pkg2_norm = json.dumps(pkg2, ensure_ascii=False, sort_keys=True)
    assert pkg1_norm == pkg2_norm, "幂等复现失败：两次调用结果存在差异"
    
    assert pkg1["analysis_id"] == "fixed-test-id"
    assert pkg1["provenance"]["insight_generated_at"] == "2026-08-25T12:00:00Z"
    assert pkg2["analysis_id"] == "fixed-test-id"
    assert pkg2["provenance"]["insight_generated_at"] == "2026-08-25T12:00:00Z"
    print("  ✓ 显式注入 analysis_id 与 generated_at 实现完全幂等复现: PASSED")


def test_idempotency_default_timestamps_generates_unique():
    """未传入 analysis_id 与 generated_at 时，必须生成新的非确定性 UUID 与时间戳。"""
    with open(REQ_EXAMPLE_PATH, "r", encoding="utf-8") as f:
        req_example = json.load(f)

    pkg1 = synthesize_from_request(req_example)
    pkg2 = synthesize_from_request(req_example)
    
    assert pkg1["analysis_id"] != pkg2["analysis_id"]
    assert pkg1["provenance"]["insight_generated_at"] != pkg2["provenance"]["insight_generated_at"]
    print("  ✓ 默认运行时自动生成 ID 与时间戳 (非确定性): PASSED")


def test_skill_md_python_example_doc_reference():
    """SKILL.md 中 Python 调用示例必须显式引用 examples/insight-request.example.json 路径。"""
    skill_md_path = SKILL_DIR / "SKILL.md"
    skill_md_text = skill_md_path.read_text(encoding="utf-8")
    assert "../examples/insight-request.example.json" in skill_md_text, \
        "SKILL.md 必须显式链接 ../examples/insight-request.example.json 权威示例"
    print("  ✓ SKILL.md 已显式链接权威示例文件: PASSED")


def run_doc_example_tests():
    print("\n============================================================")
    print("谋圣·售点洞察 权威示例配对与一致性专项测试")
    print("============================================================")
    test_examples_dir_contains_authority_json()
    test_authority_request_and_package_exact_pairing()
    test_idempotency_with_injected_timestamps()
    test_idempotency_default_timestamps_generates_unique()
    test_skill_md_python_example_doc_reference()
    print("============================================================")
    print(">>> 5/5 权威示例配对与一致性专项测试 100% 通过 <<<")
    print("============================================================\n")


if __name__ == "__main__":
    run_doc_example_tests()
