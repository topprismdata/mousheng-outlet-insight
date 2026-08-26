# -*- coding: utf-8 -*-
"""
insight_cli.py
==============
谋圣·售点洞察（Mousheng Outlet Insight）独立认识组件纯 JSON 命令行入口。
零底层数据库依赖、零 CSV 依赖、零 DataFrame 依赖。

用法示例：
  python insight_cli.py --request request.json --out package.json
  cat request.json | python insight_cli.py
"""
import argparse
import json
import sys
from pathlib import Path
from jsonschema import Draft202012Validator

from insight import synthesize_from_request

SKILL_DIR = Path(__file__).resolve().parent.parent
REQ_SCHEMA_PATH = SKILL_DIR / "references" / "insight-request.schema.json"
PKG_SCHEMA_PATH = SKILL_DIR / "references" / "insight-package.schema.json"


def main():
    parser = argparse.ArgumentParser(description="谋圣·售点洞察独立认识组件 (Pure JSON CLI)")
    parser.add_argument("--request", help="输入 InsightRequest JSON 文件路径；不传则从 stdin 读取")
    parser.add_argument("--out", help="输出 InsightPackage JSON 文件路径；不传则输出至 stdout")
    parser.add_argument("--validate", action="store_true", help="校验输出是否 100% 符合 JSON Schema")
    parser.add_argument("--pretty", action="store_true", help="格式化美化输出 JSON")

    args = parser.parse_args()

    # 1. 读取输入
    if args.request:
        req_path = Path(args.request)
        if not req_path.exists():
            print(f"Error: Request file not found: {req_path}", file=sys.stderr)
            sys.exit(1)
        with open(req_path, "r", encoding="utf-8") as f:
            request_data = json.load(f)
    else:
        try:
            request_data = json.load(sys.stdin)
        except Exception as e:
            print(f"Error reading JSON from stdin: {e}", file=sys.stderr)
            sys.exit(1)

    # 2. 强校验输入 Schema (必须合法，否则立即以退出码 2 终止，绝不生成结果)
    if not REQ_SCHEMA_PATH.exists():
        print(f"Error: Input Schema not found: {REQ_SCHEMA_PATH}", file=sys.stderr)
        sys.exit(2)
    with open(REQ_SCHEMA_PATH, "r", encoding="utf-8") as f:
        req_schema = json.load(f)
    val_req = Draft202012Validator(req_schema)
    errs_req = list(val_req.iter_errors(request_data))
    if errs_req:
        print(f"[Input Schema Rejection] InsightRequest 输入不合法 ({len(errs_req)} 处违规):", file=sys.stderr)
        for err in errs_req:
            path_str = ".".join(str(p) for p in err.path) or "root"
            print(f"  · 路径 [{path_str}]: {err.message}", file=sys.stderr)
        sys.exit(2)
    # 3. 执行纯洞察合成 (第二棱镜)
    package = synthesize_from_request(request_data)

    # 4. 校验输出 Schema
    if args.validate and PKG_SCHEMA_PATH.exists():
        with open(PKG_SCHEMA_PATH, "r", encoding="utf-8") as f:
            pkg_schema = json.load(f)
        val_pkg = Draft202012Validator(pkg_schema)
        errs_pkg = list(val_pkg.iter_errors(package))
        if errs_pkg:
            print(f"Error: Output package failed Schema validation: {errs_pkg}", file=sys.stderr)
            sys.exit(1)

    # 5. 写出结果
    indent = 2 if args.pretty else None
    out_str = json.dumps(package, ensure_ascii=False, indent=indent)

    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            f.write(out_str)
        print(f"Successfully generated InsightPackage to {out_p}")
    else:
        print(out_str)


if __name__ == "__main__":
    main()
