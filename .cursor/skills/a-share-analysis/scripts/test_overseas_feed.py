#!/usr/bin/env python3
"""Classifier checks for the overseas market list. No network."""

from __future__ import annotations

import unittest

from overseas_feed import classify, items_from_html

FIXTURE = """
<a href="https://www.news.cn/world/20261104/a58541d091da468eb33bce05b5cdb32e/c.html">美民主党连获关键州席位</a>
<a href="https://www.news.cn/world/20261104/618d960658774abfaa4a0e894883310c/c.html">或重掌国会</a>
<a href="https://www.news.cn/world/20261104/5fa0c6f07cdf4c89b7c3295281a0da7f/c.html">美民主党重掌国会 特朗普执政承重压</a>
<a href="https://www.news.cn/world/20261104/c3d17ff380a745bc847677f30e015953/c.html">特朗普：若民主党掌控国会 他将行使否决权</a>
<a href="https://www.news.cn/world/20261104/vote.html">美国举行中期选举投票</a>
<a href="https://www.news.cn/world/20261104/poll.html">民调：美民主党对共和党中期选举领先优势缩小</a>
<a href="https://www.news.cn/world/20261104/spend.html">2026年美国中期选举总支出预计达119亿美元</a>
<a href="https://www.news.cn/fortune/20261104/a94ccab9783d417bab20b01576569312/c.html">纽约股市3日收盘</a>
<a href="https://www.news.cn/world/20261103/bg.html">保加利亚现任总统约托娃赢得总统选举</a>
<a href="https://www.news.cn/world/20261104/a58541d091da468eb33bce05b5cdb32e/c.html">美民主党连获关键州席位</a>
"""


class ClassifyTest(unittest.TestCase):
    def test_session_results_are_kept(self) -> None:
        self.assertEqual(classify("美民主党连获关键州席位"), "election_result")
        self.assertEqual(classify("或重掌国会"), "election_result")
        self.assertEqual(classify("美民主党重掌国会 特朗普执政承重压"), "election_result")
        self.assertEqual(classify("美国媒体预计民主党夺回参议院重掌国会"), "election_result")

    def test_process_and_conditional_are_dropped(self) -> None:
        self.assertIsNone(classify("特朗普：若民主党掌控国会 他将行使否决权"))
        self.assertIsNone(classify("美国举行中期选举投票"))
        self.assertIsNone(classify("美国中期选举投票结束，计票工作开始"))
        self.assertIsNone(classify("民调：美民主党对共和党中期选举领先优势缩小"))
        self.assertIsNone(classify("2026年美国中期选举总支出预计达119亿美元"))
        self.assertIsNone(classify("生活成本成美国中期选举核心议题之一"))

    def test_other_countries_and_market_prints(self) -> None:
        self.assertIsNone(classify("保加利亚现任总统约托娃赢得总统选举"))
        self.assertEqual(classify("纽约股市3日收盘"), "market_print")
        self.assertEqual(classify("美元指数3日上涨0.15%"), "market_print")
        self.assertEqual(classify("3日国际油价下跌"), "market_print")
        self.assertIsNone(classify("前三季度我国黄金产量同比下降 消费量同比微增"))
        self.assertEqual(classify("苹果公司财报营收"), None)
        self.assertEqual(classify("美国公司财报营收同比增长"), "earnings")

    def test_fixture_order_and_dedupe(self) -> None:
        rows = items_from_html(FIXTURE, "world")
        self.assertEqual(
            [row["kind"] for row in rows],
            ["election_result", "election_result", "election_result", "market_print"],
        )
        urls = [row["url"] for row in rows]
        self.assertEqual(len(urls), len(set(urls)))


if __name__ == "__main__":
    unittest.main()
