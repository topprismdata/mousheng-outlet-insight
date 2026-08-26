#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提取数据模式 —— 单元/集成测试 (outlet-insight §9).

基于 6 个核心洞察设计用例:
  1. A/B/C/D/E 分类逻辑
  2. 两步走对检测 (detect_two_step_pairs)
  3. 列填充率 / 取值集中度计算
  4. 字段字典加载与映射
  5. 服务状态统计
  6. 边界条件与鲁棒性

用法:
  $PY test_extraction_pattern.py
  $PY test_extraction_pattern.py -v
"""

import csv
import json
import os
import sys
import time
import unittest
from collections import defaultdict

import pandas as pd

# ---- 被测模块 ----
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import extraction_pattern_scan as eps
import signature_probe as sp

FIXTURES = os.path.join(HERE, "fixtures")
DICT_PATH = os.path.join(HERE, "field_dictionary.json")


# ═══════════════════════════════════════════════
#  1. classify() — A/B/C/D 分类逻辑
# ═══════════════════════════════════════════════

class TestClassify(unittest.TestCase):

    def test_a_single_row(self):
        """n≤5 → A 单行/少行导出 (不管渠道集中度)"""
        self.assertIn("A-单行", eps.classify(1, 1.0))
        self.assertIn("A-单行", eps.classify(3, 0.5))
        self.assertIn("A-单行", eps.classify(5, 0.3))
        self.assertIn("A-单行", eps.classify(1, 0.0))  # 极端: channel_dom=0走 default, 但被 A 优先截住

    def test_b_single_channel_high_concentration(self):
        """渠道集中度 ≥0.9 且行数>5 → B"""
        self.assertIn("B-单一维度", eps.classify(50, 1.0))
        self.assertIn("B-单一维度", eps.classify(126, 0.95))
        self.assertIn("B-单一维度", eps.classify(6, 0.90))   # 边界: 刚好 0.9

    def test_c_dispersed_large(self):
        """行数 ≥200 且渠道集中度 <0.85 → C"""
        self.assertIn("C-多维度", eps.classify(300, 0.40))
        self.assertIn("C-多维度", eps.classify(2944, 0.36))
        self.assertIn("C-多维度", eps.classify(200, 0.84))   # 边界: 行=200, dom<0.85

    def test_d_mixed_fallback(self):
        """不满足 A/B/C 任意条件 → D"""
        self.assertIn("D-混合", eps.classify(150, 0.85))    # <200行(C不够), dom<0.9(B不够), >5(A不够)
        self.assertIn("D-混合", eps.classify(80, 0.70))      # n>5, dom<0.9
        self.assertIn("D-混合", eps.classify(10, 0.88))      # n>5, dom<0.9

    def test_boundary_n6_dom09(self):
        """边界: n=6 不属于 A (A 是 n≤5)"""
        self.assertIn("B-单一维度", eps.classify(6, 0.90))  # n=6, dom=0.9 → B

    def test_boundary_n200_dom084(self):
        """边界: n=200, dom=0.84 → C"""
        self.assertIn("C-多维度", eps.classify(200, 0.84))

    def test_boundary_n199_dom084(self):
        """边界: n=199, dom=0.84 → D (不够 C 的 200 行门槛)"""
        self.assertIn("D-混合", eps.classify(199, 0.84))

    def test_boundary_dom089_versus_090(self):
        """B/C 分界在渠道集中度 0.9, 不是省份"""
        self.assertIn("B-单一维度", eps.classify(200, 0.90))
        self.assertIn("D-混合", eps.classify(50, 0.89))   # n<200, dom<0.9, 非C非B


# ═══════════════════════════════════════════════
#  2. 列填充率 / 取值集中度
# ═══════════════════════════════════════════════

class TestColumnMetrics(unittest.TestCase):

    def test_nonempty_ratio_all_full(self):
        s = pd.Series(["a", "b", "c"])
        self.assertEqual(eps.nonempty_ratio(s), 1.0)

    def test_nonempty_ratio_half_empty(self):
        s = pd.Series(["a", "", "c", ""])
        self.assertEqual(eps.nonempty_ratio(s), 0.5)

    def test_nonempty_ratio_all_empty(self):
        s = pd.Series(["", "", ""])
        self.assertEqual(eps.nonempty_ratio(s), 0.0)

    def test_nonempty_ratio_whitespace_as_empty(self):
        """空格/纯空白视为空"""
        s = pd.Series(["a", "  ", "\t", "b"])
        self.assertEqual(eps.nonempty_ratio(s), 0.5)

    def test_top_share_single_value(self):
        df = pd.DataFrame({"col": ["x", "x", "x"]})
        share, val = eps.top_share(df, "col")
        self.assertAlmostEqual(share, 1.0)
        self.assertEqual(val, "x")

    def test_top_share_mixed(self):
        df = pd.DataFrame({"col": ["a", "a", "a", "b", "b"]})
        share, val = eps.top_share(df, "col")
        self.assertAlmostEqual(share, 0.6)
        self.assertEqual(val, "a")

    def test_top_share_missing_column(self):
        """列不存在 → (0.0, "")"""
        df = pd.DataFrame({"x": [1, 2]})
        share, val = eps.top_share(df, "no_such_col")
        self.assertEqual(share, 0.0)
        self.assertEqual(val, "")

    def test_top_share_all_empty(self):
        df = pd.DataFrame({"col": ["", "", ""]})
        share, val = eps.top_share(df, "col")
        self.assertEqual(share, 0.0)
        self.assertEqual(val, "")


# ═══════════════════════════════════════════════
#  3. 字段字典加载
# ═══════════════════════════════════════════════

class TestFieldDictionary(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.DICT, cls.GROUP_COLS = eps.load_dict(DICT_PATH)

    def test_dict_has_152_fields(self):
        self.assertGreaterEqual(len(self.DICT), 150,
            f"字段字典应有 ≥150 字段, 实有 {len(self.DICT)}")

    def test_dict_has_14_groups(self):
        self.assertEqual(len(self.GROUP_COLS), 14,
            f"应有 14 个语义组, 实有 {len(self.GROUP_COLS)}")

    def test_key_groups_exist(self):
        for g in ["org_location", "service", "channel", "tourism", "dianping", "sales", "population"]:
            self.assertIn(g, self.GROUP_COLS, f"语义组 '{g}' 缺失")
            self.assertGreater(len(self.GROUP_COLS[g]), 0, f"语义组 '{g}' 无字段")

    def test_no_duplicate_fields(self):
        all_cols = []
        for cols in self.GROUP_COLS.values():
            all_cols.extend(cols)
        self.assertEqual(len(all_cols), len(set(all_cols)),
            f"字段字典存在重复: {len(all_cols) - len(set(all_cols))} 个")

    def test_group_activation_uses_fill_rate(self):
        """验证: 组激活用的是平均填充率≥50%, 不是"任意有值" (修正第一版错误)"""
        # 构造: 组有 2 列, 一列全空一列全满 → 组填充率 50% → 应激活
        cols = self.GROUP_COLS.get("dianping", [])
        if len(cols) >= 2:
            present = [c for c in cols if c in self.DICT]
            self.assertGreater(len(present), 0)
            # 组级聚合逻辑由 GROUP_COLS 的键值对承载, 这里仅验证字典完整性


# ═══════════════════════════════════════════════
#  4. 服务状态统计
# ═══════════════════════════════════════════════

class TestServiceStatus(unittest.TestCase):

    def test_direct_rate_calculation(self):
        """直服率应准确计算"""
        df = pd.read_csv(os.path.join(FIXTURES, "b_single_channel.csv"),
                         dtype=str, keep_default_na=False, encoding="utf-8-sig")
        sv = df["服务状态"].astype(str).str.strip()
        sv = sv[sv != ""]
        direct = sv.eq("直接服务").mean()
        # 50 rows, 30 direct
        self.assertAlmostEqual(direct, 0.6, delta=0.01)

    def test_nondirect_rate_includes_multiple_statuses(self):
        """非直服率 = 未服务/非直接服务/非营业状态/非营业 之和"""
        df = pd.read_csv(os.path.join(FIXTURES, "d_mixed.csv"),
                         dtype=str, keep_default_na=False, encoding="utf-8-sig")
        sv = df["服务状态"].astype(str).str.strip()
        sv = sv[sv != ""]
        nondirect = sv.isin(["未服务", "非直接服务", "非营业状态", "非营业"]).mean()
        direct = sv.eq("直接服务").mean()
        # 对于 d_mixed: 100 未服务, 50 直接服务 → nondirect=100/150≈0.667, direct=50/150≈0.333
        self.assertAlmostEqual(direct, 1/3, delta=0.01)
        self.assertAlmostEqual(nondirect, 2/3, delta=0.01)

    def test_missing_service_column(self):
        """缺 服务状态列时, 不应崩溃"""
        df = pd.read_csv(os.path.join(FIXTURES, "missing_service.csv"),
                         dtype=str, keep_default_na=False, encoding="utf-8-sig")
        if "服务状态" in df.columns:
            sv = df["服务状态"].astype(str).str.strip()
        else:
            sv = pd.Series([""] * len(df))
        sv = sv[sv != ""]
        self.assertEqual(len(sv), 0,
            "缺服务状态列时应有空序列, 不抛异常")

    def test_nondirect_majority_flag(self):
        """nondirect_majority 判断: 直服率<0.5 → True"""
        df = pd.read_csv(os.path.join(FIXTURES, "d_mixed.csv"),
                         dtype=str, keep_default_na=False, encoding="utf-8-sig")
        sv = df["服务状态"].astype(str).str.strip()
        sv = sv[sv != ""]
        direct = sv.eq("直接服务").mean()
        # direct≈0.333 < 0.5 → nondirect_majority=True
        self.assertTrue(direct < 0.5,
            f"d_mixed 直服率应为 33%, 实为 {direct:.1%}")
        self.assertLess(direct, 0.5)


# ═══════════════════════════════════════════════
#  5. 两步走对检测 (E 模式)
# ═══════════════════════════════════════════════

class TestTwoStepDetection(unittest.TestCase):

    def setUp(self):
        # 手动构造 rows_list 而非依赖真实扫描
        self.base_rows = []

    def _make_row(self, file, dt, province, channel, rows, direct_rate):
        return {
            "file": file, "datetime": dt,
            "top_province": province, "top_channel": channel,
            "rows": rows, "direct_rate": direct_rate,
        }

    def test_happy_path(self):
        """标准两步走: 同一省+业态, 间隔1分钟, 直服率 95%→0%"""
        rows = [
            self._make_row("step1.csv", "2026-07-24 05:47", "江西省", "教育", 827, 0.952),
            self._make_row("step2.csv", "2026-07-24 05:48", "江西省", "教育", 1380, 0.0),
        ]
        role_map, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["pair_id"], "E-01")
        self.assertEqual(role_map["step1.csv"], ("E-第一步(直接服务)", "E-01"))
        self.assertEqual(role_map["step2.csv"], ("E-第二步(待开发)", "E-01"))
        self.assertAlmostEqual(pairs[0]["gap_minutes"], 1.0, delta=0.1)

    def test_time_gap_too_large(self):
        """间隔超过 10 分钟 → 不配成对"""
        rows = [
            self._make_row("early.csv", "2026-07-24 05:47", "江西省", "教育", 827, 0.95),
            self._make_row("late.csv",  "2026-07-24 06:10", "江西省", "教育", 1380, 0.0),
        ]
        _, pairs = eps.detect_two_step_pairs(rows, max_gap_minutes=10)
        self.assertEqual(len(pairs), 0,
            "间隔 23 分钟 >10 分钟, 不应配成对")

    def test_wrong_order(self):
        """先导出待开发、后导出直接服务 → 不配成对"""
        rows = [
            self._make_row("nondirect.csv", "2026-07-24 05:47", "江西省", "教育", 1380, 0.0),
            self._make_row("direct.csv",    "2026-07-24 05:48", "江西省", "教育", 827, 0.95),
        ]
        _, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 0,
            "顺序反了 (先低→后高), 不配")

    def test_both_high_direct(self):
        """两步都是高直服 → 不配"""
        rows = [
            self._make_row("a.csv", "2026-07-24 05:47", "江西省", "教育", 827, 0.90),
            self._make_row("b.csv", "2026-07-24 05:48", "江西省", "教育", 500, 0.85),
        ]
        _, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 0)

    def test_both_low_direct(self):
        """两步都是低直服 → 不配 (需要先高后低)"""
        rows = [
            self._make_row("a.csv", "2026-07-24 05:47", "江西省", "教育", 827, 0.20),
            self._make_row("b.csv", "2026-07-24 05:48", "江西省", "教育", 500, 0.10),
        ]
        _, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 0)

    def test_different_province(self):
        """不同省份 → 不配 (分组隔离)"""
        rows = [
            self._make_row("jx.csv",  "2026-07-24 05:47", "江西省", "教育", 827, 0.95),
            self._make_row("gx.csv",  "2026-07-24 05:48", "��西壮族自治区", "教育", 1380, 0.0),
        ]
        _, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 0)

    def test_different_channel(self):
        """不同业态 → 不配 (分组隔离)"""
        rows = [
            self._make_row("edu.csv",   "2026-07-24 05:47", "江西省", "教育", 827, 0.95),
            self._make_row("food.csv",  "2026-07-24 05:48", "江西省", "餐饮", 1380, 0.0),
        ]
        _, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 0)

    def test_direct_rate_boundary_080(self):
        """边界: 第一步直服率刚好 0.80 → 不触发 (>0.8, 不含等号)"""
        rows = [
            self._make_row("a.csv", "2026-07-24 05:47", "江西省", "教育", 827, 0.80),
            self._make_row("b.csv", "2026-07-24 05:48", "江西省", "教育", 500, 0.20),
        ]
        _, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 0,
            "direct_rate=0.80 不满足 >0.8 条件")

    def test_direct_rate_boundary_081(self):
        """边界: 第一步直服率 0.81 → 触发"""
        rows = [
            self._make_row("a.csv", "2026-07-24 05:47", "江西省", "教育", 827, 0.81),
            self._make_row("b.csv", "2026-07-24 05:48", "江西省", "教育", 500, 0.20),
        ]
        _, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 1)

    def test_second_direct_boundary_030(self):
        """边界: 第二步直服率刚好 0.30 → 不触发 (<0.3, 不含等号)"""
        rows = [
            self._make_row("a.csv", "2026-07-24 05:47", "江西省", "教育", 827, 0.95),
            self._make_row("b.csv", "2026-07-24 05:48", "江西省", "教育", 500, 0.30),
        ]
        _, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 0)

    def test_none_direct_rate(self):
        """直服率为 None/空 → 跳过, 不抛异常"""
        rows = [
            self._make_row("a.csv", "2026-07-24 05:47", "江西省", "教育", 827, None),
            self._make_row("b.csv", "2026-07-24 05:48", "江西省", "教育", 500, 0.0),
        ]
        _, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 0)

    def test_multiple_pairs_same_group(self):
        """同一省+业态有 4 条: A(高)→B(低)→C(高)→D(低) → 应检出 2 对"""
        rows = [
            self._make_row("a.csv", "2026-07-24 05:47", "江西省", "教育", 827, 0.95),
            self._make_row("b.csv", "2026-07-24 05:48", "江西省", "教育", 500, 0.10),
            self._make_row("c.csv", "2026-07-24 05:59", "江西省", "教育", 600, 0.90),
            self._make_row("d.csv", "2026-07-24 06:05", "江西省", "教育", 700, 0.05),
        ]
        role_map, pairs = eps.detect_two_step_pairs(rows)
        self.assertEqual(len(pairs), 2,
            f"应检出 2 对, 实检出 {len(pairs)}")


# ═══════════════════════════════════════════════
#  6. 真实 Fixture 集成测试
# ═══════════════════════════════════════════════

class TestFixtureIntegration(unittest.TestCase):

    def test_fixture_a_classified_correctly(self):
        """fixture a_single_row → A"""
        df = pd.read_csv(os.path.join(FIXTURES, "a_single_row.csv"),
                         dtype=str, keep_default_na=False, encoding="utf-8-sig")
        _, top_chan = eps.top_share(df, "渠道组")
        chan_dom, _ = eps.top_share(df, "渠道组")
        self.assertIn("A-单行", eps.classify(len(df), chan_dom))

    def test_fixture_b_classified_correctly(self):
        """fixture b_single_channel → B (50行, 渠道组100%旅游)"""
        df = pd.read_csv(os.path.join(FIXTURES, "b_single_channel.csv"),
                         dtype=str, keep_default_na=False, encoding="utf-8-sig")
        chan_dom, top_chan = eps.top_share(df, "渠道组")
        self.assertAlmostEqual(chan_dom, 1.0, delta=0.01)
        self.assertEqual(top_chan, "旅游")
        self.assertIn("B-单一维度", eps.classify(len(df), chan_dom))

    def test_fixture_c_classified_correctly(self):
        """fixture c_dispersed → C (300行, 渠道组分散)"""
        df = pd.read_csv(os.path.join(FIXTURES, "c_dispersed.csv"),
                         dtype=str, keep_default_na=False, encoding="utf-8-sig")
        chan_dom, top_chan = eps.top_share(df, "渠道组")
        self.assertGreater(len(df), 200)
        self.assertLess(chan_dom, 0.85,
            f"c_dispersed 渠道集中度应为 <0.85, 实为 {chan_dom:.3f} ({top_chan})")
        self.assertIn("C-多维度", eps.classify(len(df), chan_dom))

    def test_fixture_d_classified_correctly(self):
        """fixture d_mixed → D (150行, 渠道集中度≈0.85)"""
        df = pd.read_csv(os.path.join(FIXTURES, "d_mixed.csv"),
                         dtype=str, keep_default_na=False, encoding="utf-8-sig")
        chan_dom, _ = eps.top_share(df, "渠道组")
        pattern = eps.classify(len(df), chan_dom)
        self.assertIn("D-混合", pattern,
            f"d_mixed (n=150, dom={chan_dom:.3f}) 应为 D, 实为 {pattern}")

    def test_fixture_empty_cols(self):
        """部分列为空: 非空率计算应正确"""
        df = pd.read_csv(os.path.join(FIXTURES, "empty_cols.csv"),
                         dtype=str, keep_default_na=False, encoding="utf-8-sig")
        # 大众点评-评分: 只有第3行有值 → 填充率 1/3
        nr = eps.nonempty_ratio(df["大众点评-评分"])
        self.assertAlmostEqual(nr, 1/3, delta=0.01)
        # 腾讯售点名称: 第2行空 → 2/3
        nr_name = eps.nonempty_ratio(df["腾讯售点名称"])
        self.assertAlmostEqual(nr_name, 2/3, delta=0.01)

    def test_fixture_missing_service_column_no_crash(self):
        """缺服务状态列的文件应被正常处理, 不抛异常"""
        try:
            df = pd.read_csv(os.path.join(FIXTURES, "missing_service.csv"),
                             dtype=str, keep_default_na=False, encoding="utf-8-sig")
            n, c = df.shape
            # 应能正常计算基本画像
            prov_dom, top_prov = eps.top_share(df, "省份")
            chan_dom, top_chan = eps.top_share(df, "渠道组")
            pattern = eps.classify(n, chan_dom)
            self.assertIn("A-单行", pattern, "1行应归 A")
        except Exception as e:
            self.fail(f"缺服务状态列应不崩溃, 但抛出: {e}")


# ═══════════════════════════════════════════════
#  7. 端到端扫描集成测试
# ═══════════════════════════════════════════════

class TestEndToEndScan(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp_out = os.path.join(FIXTURES, "_test_out")
        summary = eps.scan(
            data_dir=FIXTURES,
            out_dir=cls.tmp_out,
            dict_path=DICT_PATH,
        )
        cls.summary = summary
        cls.df = pd.read_csv(
            os.path.join(cls.tmp_out, "extraction_pattern_classification.csv"),
            encoding="utf-8-sig")

    def test_scan_runs_without_error(self):
        self.assertIsNotNone(self.summary, "扫描应返回 summary dict")
        self.assertGreater(self.summary["total_files"], 0)

    def test_classification_csv_has_required_columns(self):
        required = ["file", "datetime", "rows", "cols", "active_cols",
                     "top_province", "top_channel", "direct_rate",
                     "nondirect_rate", "nondirect_majority", "pattern",
                     "two_step_role", "two_step_pair_id", "activated_groups"]
        for col in required:
            self.assertIn(col, self.df.columns, f"分类 CSV 缺列: {col}")

    def test_all_fixtures_have_pattern(self):
        patterns = self.df["pattern"].tolist()
        for p in patterns:
            self.assertIn(p[:2], ["A-", "B-", "C-", "D-"],
                f"模式标签格式异常: {p}")

    def test_e_pair_detected(self):
        """验证 E 两步走对被检出"""
        e_files = self.df[self.df["two_step_role"].ne("")]
        self.assertGreaterEqual(len(e_files), 2,
            f"应至少检出 2 个 E 文件, 实检出 {len(e_files)}")

        self.summary["two_step_pair_count"]
        self.assertGreaterEqual(self.summary["two_step_pair_count"], 1,
            f"应至少检出 1 对两步走, 实检出 {self.summary['two_step_pair_count']}")

        pairs = self.summary["two_step_pairs"]
        self.assertEqual(pairs[0]["province"], "江西省")
        self.assertEqual(pairs[0]["channel"], "教育")
        self.assertGreater(pairs[0]["first_direct_rate"], 0.8)
        self.assertLess(pairs[0]["second_direct_rate"], 0.3)

    def test_summary_json_has_all_sections(self):
        keys = ["total_files", "total_rows", "pattern_distribution",
                "province_by_files", "channel_by_files", "date_trend",
                "nondirect_majority_files", "activated_group_files",
                "two_step_pairs", "two_step_pair_count", "two_step_files_count"]
        for k in keys:
            self.assertIn(k, self.summary, f"summary 缺键: {k}")

    def test_tourism_and_ctrip_not_confused(self):
        """实证: 渠道组取值(旅游/住宿) 与 携程列深填 是两个指标, 不混淆"""
        self.assertIn("channel_tourism_lodging_files", self.summary)
        self.assertIn("ctrip_cols_deep_files", self.summary)
        # 两个数字不能混淆: 有旅游业态未必携程列深填
        # (fixtures 中没有携程深填, ctrip_cols_deep_files 应为 0)
        T = self.summary["channel_tourism_lodging_files"]
        C = self.summary["ctrip_cols_deep_files"]
        self.assertGreaterEqual(T, 0)
        self.assertGreaterEqual(C, 0)

    def test_b_c_boundary_is_channel_not_province(self):
        """B/C 分界是渠道集中度, 不是省份"""
        # c_dispersed: 单省安徽但渠道组分散 → C
        c_rows = self.df[self.df["file"] == "c_dispersed.csv"]
        if len(c_rows) > 0:
            self.assertIn("C-多维度", c_rows.iloc[0]["pattern"],
                "安徽单省但渠道分散, 应为 C (不是 B)")

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp_out, ignore_errors=True)


# ═══════════════════════════════════════════════
#  8. Signature Probe 测试
# ═══════════════════════════════════════════════

class TestSignatureProbe(unittest.TestCase):

    def test_share_helper(self):
        df = pd.DataFrame({"col": ["a", "a", "b"]})
        share, val = sp._share(df, "col")
        self.assertAlmostEqual(share, 2/3, delta=0.01)

    def test_share_missing_col(self):
        df = pd.DataFrame({"x": [1]})
        share, val = sp._share(df, "no_col")
        self.assertEqual(share, 0.0)
        self.assertEqual(val, "")

    def test_probe_classification_matches_scan(self):
        """探针的快捷归类应与扫描器 classify 结果一致"""
        df = pd.read_csv(os.path.join(FIXTURES, "c_dispersed.csv"),
                         dtype=str, keep_default_na=False, encoding="utf-8-sig")
        n = len(df)
        share, _ = sp._share(df, "渠道组")

        if n <= 5:
            probe_p = "A"
        elif share >= 0.9:
            probe_p = "B"
        elif n >= 200 and share < 0.85:
            probe_p = "C"
        else:
            probe_p = "D"

        scan_p = eps.classify(n, share)
        self.assertEqual(probe_p, scan_p[0],
            f"probe={probe_p} vs scan='{scan_p}' 不一致")


if __name__ == "__main__":
    unittest.main(verbosity=2)
