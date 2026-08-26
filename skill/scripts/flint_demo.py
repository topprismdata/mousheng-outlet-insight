"""flint_demo.py — 端到端验证：DM 统计查询 → Flint 图形化

跑通这条链路，证明 sh-store-insight 的查询结果可以直接图形化：
  1) 用 DM 层做统计查询（上海市 餐饮 连锁品牌 按门店数 TOP20）
  2) viz_adapter 把结果转成 Flint ChartAssemblyInput
  3) render_flint.mjs 编译成 ECharts 自包含 HTML
  4) 产物落在 output/sh_query_tests/

用法:
  python flint_demo.py            # 默认跑品牌TOP20 + 渲染 echarts
  python flint_demo.py --backend plotly
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from dm_layer import query_stat
from viz_adapter import build_and_write

HERE = Path(__file__).resolve().parent
NODE = "/Users/ghb/.workbuddy/binaries/node/versions/22.22.2/bin/node"
NODE_PATH = "/Users/ghb/.workbuddy/binaries/node/workspace/node_modules"
OUT_DIR = Path("/Users/ghb/sh-store-insight/output/sh_query_tests")
DB = "/Users/ghb/sh-store-insight/data/sh_dm.sqlite"


def _render(spec_path: Path, html_path: Path, backend: str, palette: str, theme: str):
    env = {**__import__("os").environ, "NODE_PATH": NODE_PATH}
    r = subprocess.run(
        [NODE, str(HERE / "render_flint.mjs"), str(spec_path), str(html_path),
         backend, palette, theme],
        capture_output=True, text=True, env=env,
    )
    if r.returncode != 0:
        print("   渲染失败:", r.stderr, file=sys.stderr)
        raise RuntimeError(r.stderr)
    print(f"   渲染 -> {html_path.name}  ({r.stdout.strip()})")


def run_demo(backend: str = "echarts") -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 1) DM 统计查询：上海市(上海城区) 餐饮 连锁品牌 按门店数 TOP20 ----
    df, note = query_stat(
        DB,
        group_by="连锁品牌名称",
        filters={"城市": "上海城区", "渠道组": "餐饮", "是否连锁": "是"},
        order_by="cnt",
        limit=20,
    )
    print(f"[1] DM 统计: {len(df)} 行  note={note}")

    # ---- 2) 适配器：结果 → Flint spec ----
    spec_path = OUT_DIR / "flint_spec_品牌TOP20.json"
    build_and_write(
        df,
        spec_path,
        x="dim_value",
        y="cnt",
        chart_type="Bar Chart",
        title="上海市餐饮连锁品牌门店数 TOP20",
    )
    print(f"[2] Flint spec -> {spec_path.name}")

    # ---- 3) node 桥：spec → HTML ----
    # 3a) before：Flint 裸输出（无配色/无主题），用于对比
    before = OUT_DIR / f"flint_品牌TOP20_{backend}_before.html"
    print("[3a] 渲染裸输出(before)...")
    _render(spec_path, before, backend, "none", "none")

    # 3b) after：带智能配色(领奖台) + 亮色主题
    after = OUT_DIR / f"flint_品牌TOP20_{backend}.html"
    print("[3b] 渲染配色+主题(after)...")
    _render(spec_path, after, backend, "auto", "light")
    return after


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="echarts",
                    choices=["echarts", "plotly", "vegalite"])
    args = ap.parse_args()
    out = run_demo(args.backend)
    print("\n完成，打开查看:", out)
