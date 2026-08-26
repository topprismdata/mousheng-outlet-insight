#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提取数据模式扫描器 (outlet-insight · 第9块能力)。

对「一批 售点洞察*.csv 导出」做**客观的结构模式刻画**，不推断业务人员的查询动机或决策目的。
核心方法论 (用户实证, 零 LLM):

  导出模式 = 行数 + 维度列取值集中度 + 各列填充率

  - 维度列 (渠道组/经营场所/省份/大区): 导出全 <n_cols> 列表头, 非空率永远≈100%,
    客观判别力在【取值集中度】—— 单值=单一维度主导, 多值=多维度分散。
  - enrichment 列 (携程/点评/连锁/销售/拜访): 客观判别力在【填充率】—— 填充率>0=本次导出带了这一列。

范围边界: 本脚本只描述「导出长什么样」(行数/列填充/维度分布), 不对「客户为什么查、要拿去干什么」下结论。
          动机类解读属于臆测, 不在本能力范围内。

用法:
  $PY extraction_pattern_scan.py --data-dir /path/to/exports --out-dir /tmp/ep
  $PY extraction_pattern_scan.py            # 默认扫 ~/outlet-insight/data/exports_89 下的 售点洞察*.csv
输出:
  <out>/extraction_pattern_classification.csv   逐文件结构画像
  <out>/extraction_pattern_summary.json        聚合结构分布
"""
import argparse, json, os, glob, re
from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.expanduser("~/outlet-insight/data/exports_89")
DEFAULT_DICT = os.path.join(HERE, "field_dictionary.json")


def load_dict(path):
    with open(path, encoding="utf-8") as f:
        DICT = json.load(f)["fields"]
    GROUP_COLS = defaultdict(list)
    for col, info in DICT.items():
        GROUP_COLS[info["group"]].append(col)
    return DICT, GROUP_COLS


def nonempty_ratio(series):
    s = series.astype(str).str.strip()
    return float((s != "").mean())


def top_share(df, col):
    """返回 (单值占比, 单值) ; 列不存在/全空 → (0.0, '')"""
    if col not in df.columns:
        return 0.0, ""
    s = df[col].astype(str).str.strip()
    s = s[s != ""]
    if len(s) == 0:
        return 0.0, ""
    vc = s.value_counts(normalize=True)
    return float(vc.iloc[0]), str(vc.index[0])


def classify(n, chan_dom):
    """仅按客观结构特征归类, 不含意图解读。"""
    # A 单行/少行: 一次只导出极少门店
    if n <= 5:
        return "A-单行/少行导出"
    # B 单一维度主导: 单一业态取值占绝大多数
    if chan_dom >= 0.9 and n > 5:
        return "B-单一维度主导导出"
    # C 多维度分散: 行多且业态混合 (单省也算)
    if n >= 200 and chan_dom < 0.85:
        return "C-多维度分散导出"
    return "D-混合导出"


def detect_two_step_pairs(rows_list, max_gap_minutes=10):
    """检测「先直接服务 → 后待开发」两步走导出对。

    分组: 同一省份+业态, 按时间排序。
    判定: 相邻两条, 前者 direct_rate > 0.8, 后者 direct_rate < 0.3, 时间差 ≤ max_gap_minutes。
    返回:
      - role_map: {filename: (role_code, pair_id)}
        role_code: "E-第一步(直接服务)" / "E-第二步(待开发)"
      - pair_details: [{pair_id, province, channel, first_file, first_time, first_rows,
                         first_direct_rate, second_file, second_time, second_rows,
                         second_direct_rate, gap_minutes}]
    """
    from collections import defaultdict

    # Group by (province, channel)
    groups = defaultdict(list)
    for r in rows_list:
        groups[(r["top_province"], r["top_channel"])].append(r)

    role_map = {}
    pair_details = []
    pair_id = 0

    for (province, channel), files in groups.items():
        files.sort(key=lambda x: x["datetime"])
        for i in range(len(files) - 1):
            a, b = files[i], files[i + 1]
            dr_a = a.get("direct_rate")
            dr_b = b.get("direct_rate")
            if dr_a is None or dr_b is None or dr_a == "" or dr_b == "":
                continue
            if not (float(dr_a) > 0.8 and float(dr_b) < 0.3):
                continue
            # Check time gap
            from datetime import datetime as _dt
            try:
                t1 = _dt.strptime(a["datetime"], "%Y-%m-%d %H:%M")
                t2 = _dt.strptime(b["datetime"], "%Y-%m-%d %H:%M")
            except (ValueError, KeyError):
                continue
            gap = (t2 - t1).total_seconds() / 60
            if gap > max_gap_minutes:
                continue

            pair_id += 1
            pid = f"E-{pair_id:02d}"
            role_map[a["file"]] = ("E-第一步(直接服务)", pid)
            role_map[b["file"]] = ("E-第二步(待开发)", pid)
            pair_details.append({
                "pair_id": pid,
                "province": province,
                "channel": channel,
                "first_file": a["file"],
                "first_time": a["datetime"],
                "first_rows": int(a["rows"]),
                "first_direct_rate": float(dr_a),
                "second_file": b["file"],
                "second_time": b["datetime"],
                "second_rows": int(b["rows"]),
                "second_direct_rate": float(dr_b),
                "gap_minutes": round(gap, 1),
            })

    return role_map, pair_details


def scan(data_dir, out_dir, dict_path):
    os.makedirs(out_dir, exist_ok=True)
    DICT, GROUP_COLS = load_dict(dict_path)
    FILES = sorted(glob.glob(os.path.join(data_dir, "售点洞察*.csv")))
    if not FILES:
        print(f"[warn] 在 {data_dir} 下未找到 售点洞察*.csv")
        return None

    rows_out, errs = [], []
    for f in FILES:
        fn = os.path.basename(f)
        try:
            df = pd.read_csv(f, dtype=str, keep_default_na=False, encoding="utf-8-sig")
            n, c = df.shape
            nb = df.apply(lambda s: (s.astype(str).str.strip() != "")).mean()
            active = int((nb > 0).sum())
            active_ratio = round(active / c, 3) if c else 0

            m = re.search(r"(\d{13})", fn)
            ts = int(m.group(1)) if m else 0
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else ""

            prov_dom, top_prov = top_share(df, "省份")
            chan_dom, top_chan = top_share(df, "渠道组")
            sv_dom, top_sv = top_share(df, "服务状态")

            sv = df["服务状态"].astype(str).str.strip() if "服务状态" in df.columns else pd.Series([""] * n)
            sv = sv[sv != ""]
            direct = sv.eq("直接服务").mean() if len(sv) else None
            nondirect = sv.isin(["未服务", "非直接服务", "非营业状态", "非营业"]).mean() if len(sv) else 0
            # 客观事实: 该导出里非直服售点占多数 (非直服占比>=50%)
            nondirect_majority = bool(direct is not None and direct < 0.5)

            # L2 解码: 哪些语义组的列被「深拉」(组平均填充率>=50%) —— 客观填充率, 非"任意有值"
            group_depth = {}
            for g, cols in GROUP_COLS.items():
                present = [col for col in cols if col in df.columns]
                if not present:
                    continue
                d = sum(nonempty_ratio(df[col]) for col in present) / len(present)
                group_depth[g] = round(d, 2)
            activated = [g for g, d in group_depth.items() if d >= 0.5]
            tourism_cols_deep = group_depth.get("tourism", 0) >= 0.5
            dianping_cols_deep = group_depth.get("dianping", 0) >= 0.5

            pattern = classify(n, chan_dom)
            store = ""
            if n <= 5 and "腾讯售点名称" in df.columns:
                store = " / ".join(df["腾讯售点名称"].astype(str).str.strip().head(5).tolist())

            rows_out.append(dict(
                file=fn, datetime=dt, rows=n, cols=c,
                active_cols=active, active_ratio=active_ratio,
                top_province=top_prov, province_dom=round(prov_dom, 3),
                top_channel=top_chan, channel_dom=round(chan_dom, 3),
                top_service=top_sv, direct_rate=(round(direct, 3) if direct is not None else ""),
                nondirect_rate=round(float(nondirect), 3),
                nondirect_majority=nondirect_majority,
                tourism_cols_deep=tourism_cols_deep, dianping_cols_deep=dianping_cols_deep,
                activated_groups="|".join(activated),
                pattern=pattern, store=store,
                two_step_role="", two_step_pair_id=""))
        except Exception as e:
            errs.append((fn, str(e)[:140]))

    # E 模式: 检测「先直接服务 → 后待开发」两步走导出对
    two_step_map, two_step_pairs = detect_two_step_pairs(rows_out)
    for r in rows_out:
        if r["file"] in two_step_map:
            r["two_step_role"], r["two_step_pair_id"] = two_step_map[r["file"]]

    dfn = pd.DataFrame(rows_out)
    dfn.to_csv(os.path.join(out_dir, "extraction_pattern_classification.csv"), index=False, encoding="utf-8-sig")

    def agg_counter(series, weight):
        d = {}
        for v, w in zip(series, weight):
            if v in ("", None):
                continue
            d[v] = d.get(v, 0) + w
        return dict(sorted(d.items(), key=lambda x: -x[1]))

    prov_files = dfn["top_province"].value_counts()
    prov_rows = agg_counter(dfn["top_province"], dfn["rows"])
    chan_files = dfn["top_channel"].value_counts()
    chan_rows = agg_counter(dfn["top_channel"], dfn["rows"])
    pattern_files = dfn["pattern"].value_counts()
    dates = dfn["datetime"].str.slice(0, 10).value_counts().sort_index()

    grp_files = {}
    for g in GROUP_COLS:
        grp_files[g] = int(dfn["activated_groups"].str.contains(rf"(?:^|\|){g}(?:\||$)", regex=True).sum())

    summary = dict(
        total_files=len(dfn), total_rows=int(dfn["rows"].sum()),
        pattern_distribution=pattern_files.to_dict(),
        province_by_files=prov_files.to_dict(),
        province_by_rows=prov_rows,
        channel_by_files=chan_files.to_dict(),
        channel_by_rows=chan_rows,
        date_trend=dates.to_dict(),
        # 客观事实, 不混淆:
        #  - 渠道组取值∈{旅游,住宿} 的文件数 (维度列取值分布)
        #  - 携程列真正深填(填充率>=50%) 的文件数 (仅简版变体, 与上面不同)
        channel_tourism_lodging_files=int(chan_files.get("旅游", 0) + chan_files.get("住宿", 0)),
        ctrip_cols_deep_files=int(dfn["tourism_cols_deep"].sum()),
        dianping_cols_deep_files=int(dfn["dianping_cols_deep"].sum()),
        nondirect_majority_files=int(dfn["nondirect_majority"].sum()),
        activated_group_files={g: grp_files[g] for g in sorted(grp_files, key=lambda x: -grp_files[x]) if grp_files[g]},
        single_point_stores=[s for s in dfn[dfn["pattern"].str.startswith("A")]["store"].tolist() if s],
        nondirect_majority_list=dfn[dfn["nondirect_majority"]][["file", "datetime", "top_province", "top_channel", "rows", "direct_rate", "nondirect_rate"]].to_dict("records"),
        two_step_pairs=two_step_pairs,
        two_step_pair_count=len(two_step_pairs),
        two_step_files_count=int(dfn["two_step_role"].ne("").sum()),
    )
    json.dump(summary, open(os.path.join(out_dir, "extraction_pattern_summary.json"), "w"), ensure_ascii=False, indent=2)

    print(f"扫描完成: {len(dfn)} 文件, {int(dfn['rows'].sum()):,} 行; 错误 {len(errs)}")
    print("结构分类:", summary["pattern_distribution"])
    print("省份(文件数):", summary["province_by_files"])
    print("业态(文件数):", summary["channel_by_files"])
    print("旅游/住宿业态文件数(渠道组取值):", summary["channel_tourism_lodging_files"],
          " 携程列深填:", summary["ctrip_cols_deep_files"],
          " 点评列深填:", summary["dianping_cols_deep_files"],
          " 非直服占多数:", summary["nondirect_majority_files"])
    print("语义组激活(文件数):", summary["activated_group_files"])
    print("日期趋势:", summary["date_trend"])
    print("E 两步走导出对:", summary["two_step_pair_count"], "对 (",
          summary["two_step_files_count"], "个文件)")
    for p in two_step_pairs:
        print(f"  {p['pair_id']} {p['province']}×{p['channel']}: "
              f"第1步 {p['first_rows']}行 直服{p['first_direct_rate']:.0%} "
              f"→ 第2步 {p['second_rows']}行 直服{p['second_direct_rate']:.0%} "
              f"(间隔{p['gap_minutes']}分钟)")
    if errs:
        print("错误样本:", errs[:5])
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="售点洞察提取数据模式扫描器")
    ap.add_argument("--data-dir", default=DEFAULT_DATA, help="含 售点洞察*.csv 的目录 (默认 ~/outlet-insight/data/exports_89)")
    ap.add_argument("--out-dir", default=os.path.expanduser("~/outlet-insight/output/extraction_pattern_out"), help="结果输出目录")
    ap.add_argument("--dict", default=DEFAULT_DICT, help="L2 字段字典路径")
    args = ap.parse_args()
    scan(args.data_dir, args.out_dir, args.dict)
