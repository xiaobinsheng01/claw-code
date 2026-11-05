#!/usr/bin/env python3
"""Checks for the compact intraday readers. No network."""

from __future__ import annotations

import unittest

from info_diff import classify_link, links_from_html, pack
from intraday_brief import pair, path_text, segment
from intraday_screen import assign, both_averages, judge_holders


def _holder(name: str, change: str = "", ratio=1.0) -> dict:
    return {"name": name, "change": change, "ratio": ratio}


def _period(day: str, names: list[tuple[str, str]]) -> dict:
    return {"day": day, "holders": [_holder(name, change) for name, change in names]}


TEN = tuple(f"股东{i}" for i in range(10))


class BriefTest(unittest.TestCase):
    def test_segment_uses_the_four_written_bands(self) -> None:
        self.assertEqual(segment(3692, 3590.80, 3686.17), "昨日高点之上")
        self.assertEqual(segment(3660, 3590.80, 3686.17), "中点到昨日高点")
        self.assertEqual(segment(3600, 3590.80, 3686.17), "昨日低点到中点")
        self.assertEqual(segment(3500, 3590.80, 3686.17), "昨日低点之下")

    def test_a_crossed_high_keeps_the_upper_path(self) -> None:
        self.assertEqual(path_text(3686.17, 3701.59, False, True, True), "收在3686.17之上")
        self.assertEqual(path_text(3686.17, 3686.17, False, True, True), "窄区间")

    def test_bank_and_electronics(self) -> None:
        self.assertEqual(pair(-1.6, 2.9), "相反")
        self.assertEqual(pair(-1.0, -0.2), "同向或平盘")
        self.assertEqual(pair(0, 1.0), "同向或平盘")


class ScreenTest(unittest.TestCase):
    def test_both_averages_require_the_live_price(self) -> None:
        closes = [10] * 10
        self.assertEqual(both_averages(closes, 10.01)["state"], "above")
        self.assertEqual(both_averages(closes, 10)["state"], "below")
        self.assertEqual(both_averages(closes[:9], 11)["state"], "unchecked")

    def test_chip_names_are_not_counted_again_on_electronics(self) -> None:
        members = {
            "pt01801081": [{"code": "002371", "name": "北方华创"}, {"code": "688981", "name": "中芯"}],
            "pt01801080": [{"code": "002371", "name": "北方华创"}, {"code": "002415", "name": "海康"}],
            "pt01801770": [{"code": "002415", "name": "海康"}, {"code": "600050", "name": "联通"}],
            "pt01801750": [{"code": "600050", "name": "联通"}, {"code": "300033", "name": "同花顺"}],
        }
        assigned = assign(members)
        self.assertEqual([row["code"] for row in assigned["000685"]], ["002371"])
        self.assertEqual([row["code"] for row in assigned["399811"]], ["002415"])
        self.assertEqual([row["code"] for row in assigned["399389"]], ["600050"])
        self.assertEqual(assigned["000993"], [])

    def test_new_state_capital_passes_only_when_the_structure_is_simple(self) -> None:
        plain = [(name, "") for name in TEN]
        current = plain[:9] + [("国新宏盛投资(北京)有限公司", "新进")]
        prior = [(name, "") for name in TEN]
        passed = judge_holders([_period("2026-09-30", current), _period("2026-06-30", prior)])
        self.assertEqual(passed["simple"], "简单")
        self.assertEqual(passed["state"], "有")
        self.assertEqual(passed["hits"][0]["name"], "国新宏盛投资(北京)有限公司")

        mixed = list(current)
        mixed[0] = ("某投资合伙企业(有限合伙)", "")
        blocked = judge_holders([_period("2026-09-30", mixed), _period("2026-06-30", prior)])
        self.assertEqual(blocked["simple"], "复杂")
        self.assertEqual(blocked["state"], "有")

    def test_a_state_holder_that_is_not_marked_new_stays_unchecked(self) -> None:
        current = [(name, "") for name in TEN[:9]] + [("全国社保基金一零一组合", "加仓")]
        # Prior period does not contain that name, and the change text is not 新进.
        prior = [(name, "") for name in TEN]
        cell = judge_holders([_period("2026-09-30", current), _period("2026-06-30", prior)])
        self.assertEqual(cell["state"], "未核")


class DiffTest(unittest.TestCase):
    def test_known_address_and_untitled_clock(self) -> None:
        ledger = "已记 https://www.news.cn/fortune/20261105/aaaaaaaaaaaaaaaaaaaa/c.html 美国9月贸易逆差维持在相对高位"
        html = """
        <a href="/fortune/20261105/aaaaaaaaaaaaaaaaaaaa/c.html">美国9月贸易逆差维持在相对高位</a>
        11-05 10:40 <a href="/fortune/20261105/bbbbbbbbbbbbbbbbbbbb/c.html">央行公开市场</a>
        <a href="/fortune/20261105/cccccccccccccccccccc/c.html">进博会开幕式举行</a>
        <a href="/fortune/index.html">财经首页</a>
        """
        rows = links_from_html(html, "https://www.news.cn", ledger, "新华网")
        statuses = {row["url"].rsplit("/", 2)[-2]: row["status"] for row in rows}
        self.assertNotIn("aaaaaaaaaaaaaaaaaaaa", statuses)
        self.assertEqual(statuses["bbbbbbbbbbbbbbbbbbbb"], "待打开")
        self.assertEqual(statuses["cccccccccccccccccccc"], "无发布时间")
        self.assertFalse(any(row["url"].endswith("index.html") for row in rows))

    def test_commentary_and_older_clocks_are_not_this_window(self) -> None:
        self.assertEqual(
            classify_link(
                "投资机构：短期波动不改长期价值",
                "https://www.cs.com.cn/2026/11/05/detail_1.html",
                "11-05 08:01",
                "",
                cursor="2026-11-05 10:25",
            ),
            "不进W1–W5",
        )
        self.assertEqual(
            classify_link(
                "二连浩特口岸中欧班列年内通行量突破4000列",
                "https://www.cs.com.cn/2026/11/05/detail_2.html",
                "11-05 08:00",
                "",
                cursor="2026-11-05 10:25",
            ),
            "缺口",
        )

    def test_a_four_character_stem_already_in_the_ledger_is_the_same_fact(self) -> None:
        self.assertEqual(
            classify_link(
                "二连浩特口岸中欧班列年内通行量突破4000列",
                "https://www.cs.com.cn/2026/11/05/detail_2.html",
                "11-05 08:00",
                "08:00 二连浩特班列",
                cursor="2026-11-05 10:25",
            ),
            "同一事实已记",
        )

    def test_same_title_is_not_opened(self) -> None:
        self.assertEqual(
            classify_link("进博会开幕式举行", "https://www.news.cn/fortune/20261105/dddddddddddddddddddd/c.html", "2026-11-05", "进博会开幕式举行"),
            "同一事实已记",
        )

    def test_open_cap_keeps_the_newer_clock(self) -> None:
        rows = [
            {"status": "待打开", "clock": f"11-05 10:{minute:02d}", "title": str(minute), "url": str(minute), "sector": False}
            for minute in range(30)
        ]
        packed = pack(rows, open_limit=20)
        self.assertEqual(len(packed["open"]), 20)
        self.assertEqual(packed["open"][0]["clock"], "11-05 10:29")
        self.assertEqual(packed["open_left"], 10)


if __name__ == "__main__":
    unittest.main()
