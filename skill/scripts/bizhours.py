"""营业时间语义解析 —— 把自由文本营业时间变成可计算的时间谓词。

数据里的营业时间来自「大众点评营业时间」列，格式举例：
    "周一至周日 10:00-22:00"
    "周一至周日 11:00-02:00"          # 过半夜（次日 02:00 打烊）
    "周一至周日 10:00-24:00"          # 24:00 = 午夜
    "周一至周日 10:00-13:30 16:00-24:00"   # 午休，多段
    "周一 10:00-21:00  周二至周日 10:00-22:00"  # 分日

本模块只做一件事：给定一行营业时间文本 + 一个参考时刻（24h 小时数），
判断「该门店是否满足『营业到参考时刻以后 / 参考时刻以后才开门』」。

零依赖，无 pandas / numpy import —— 便于单元测试与在 match_one 内逐行调用。
"""

from __future__ import annotations

import re

# 语义层物理字段名（与 field_registry.json 对齐）
BIZ_HOURS_FIELD = "大众点评营业时间"

# 提取所有 "HH:MM-HH:MM" 段（容忍段间空格 / 中文）
_SEG_RE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


def _segments(val: str) -> list[tuple[int, int, int, int]]:
    """返回 [(开时,开分,闭时,闭分), ...]，解析失败返回 []。"""
    if not isinstance(val, str):
        return []
    out = []
    for oh, om, ch, cm in _SEG_RE.findall(val):
        try:
            out.append((int(oh), int(om), int(ch), int(cm)))
        except ValueError:
            continue
    return out


def close_after(val, ref_hour: float) -> bool:
    """该门店是否『营业到 ref_hour 点以后』才打烊。

    - 任一营业段闭店时刻 > ref_hour  → True
    - 过半夜段（闭店小时 < 开店小时，如 11:00-02:00）→ 必 True
    - 闭店 24:00（午夜）→ True
    - 闭店正好等于 ref_hour（如 22:00 整）→ False（不算『以后』）
    """
    for oh, _om, ch, cm in _segments(val):
        close = ch + cm / 60.0
        if ch < oh:                      # 过半夜
            return True
        if ch == 24:                     # 午夜 24:00
            return True
        if close > ref_hour:
            return True
    return False


def open_after(val, ref_hour: float) -> bool:
    """该门店是否『ref_hour 点以后才开门』（夜宵店句式）。

    仅当开业段的开店时刻 >= ref_hour 时成立；过半夜段（从傍晚开到凌晨）
    若开店时刻 < ref_hour 则不计入（语义上『凌晨还在开』≠『X点后才开门』）。
    """
    for oh, _om, ch, _cm in _segments(val):
        open_h = oh + _om / 60.0
        if ch < oh:                      # 过半夜段：开店在傍晚，不视为『以后才开门』
            continue
        if open_h >= ref_hour:
            return True
    return False


# —— 自然语言中 12 小时制 → 24 小时制的辅助 ——
_PM_WORDS = ("晚上", "夜里", "夜")
_AM_WORDS = ("凌晨", "上午", "早上", "中午")


def to_24h(hour: int, period: str | None) -> int:
    """把口语里的『X点』按时段词映射到 24 小时制。

    - 晚上/夜里/夜：+12（10 点 → 22）
    - 凌晨/上午/早上/中午：保持（凌晨 2 点 → 2；中午 12 点 → 12）
    - 无时段词：默认视为晚上（PM），+12
    """
    if period in _PM_WORDS:
        return hour + 12 if hour < 12 else hour
    if period in _AM_WORDS:
        return 0 if hour == 12 else hour
    # 无时段词：餐饮闭店语境默认晚上
    return hour + 12 if hour <= 12 else hour


# 解析器用的正则：捕获 (时段词, 小时)
_TIME_RE = re.compile(
    r"(?:营业|开(?:门|业|店)?|打烊|关门|闭店)?\s*到?\s*"
    r"(凌晨|夜里|晚上|夜|中午|上午|早上)?\s*"
    r"(\d{1,2})\s*点\s*(?:以后|后|之后|及以后|往后)"
)
# 夜宵店句式：X点以后才开门/开张/开店
_OPEN_RE = re.compile(
    r"(\d{1,2})\s*点\s*(?:以后|后|之后)\s*(?:才)?\s*(?:开门|开张|开店|开)"
)
# 全天营业
_ALLDAY_RE = re.compile(r"(?:24\s*小时|全天营业|通宵|24h)")


def parse_time_predicate(text: str) -> dict | None:
    """从自然语言 query 抽取营业时间谓词，返回 filter dict 或 None。

    优先级：全天营业 > 开门(夜宵) > 闭店(晚X点后)。
    """
    if _ALLDAY_RE.search(text):
        return {"field": BIZ_HOURS_FIELD, "op": "close_after", "value": 24}
    m = _OPEN_RE.search(text)
    if m:
        hour = int(m.group(1))
        return {"field": BIZ_HOURS_FIELD, "op": "open_after", "value": to_24h(hour, None)}
    m = _TIME_RE.search(text)
    if m:
        period = m.group(1)
        hour = int(m.group(2))
        return {"field": BIZ_HOURS_FIELD, "op": "close_after", "value": to_24h(hour, period)}
    return None
