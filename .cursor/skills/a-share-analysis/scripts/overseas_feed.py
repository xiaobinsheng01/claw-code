#!/usr/bin/env python3
"""List overseas-market items from Xinhua world and finance pages.

Stdlib only. Does not score W1–W8, does not write the ledger, and does not
emit an order. The agent still opens each page and copies one sentence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request

WORLD_URL = "https://www.news.cn/world/index.html"
FORTUNE_URL = "https://www.news.cn/fortune/gundong/index.html"

PARTY = ("民主党", "共和党", "众议院", "参议院", "国会")
OUTCOME = ("席位", "控制权", "重掌", "夺回", "赢得", "胜选", "掌控")
BLOCK = ("若", "如果", "民调", "投票", "计票", "支出", "议题")
MARKET = (
    "道指",
    "标普",
    "纳指",
    "美股",
    "欧股",
    "富时",
    "DAX",
    "CAC",
    "日经",
    "恒生",
    "美元指数",
    "美债",
    "油价",
    "金价",
    "沪铜",
    "铜价",
    "美联储",
)
MARKET_PLACE = ("美国", "纽约", "欧洲", "伦敦", "东京", "香港", "日本")
EARNINGS = ("营收", "净利润", "财报", "并购")
EARNINGS_WHERE = ("美国", "美股", "纽约", "欧洲", "日本", "香港")

LINK_RE = re.compile(
    r"""<a[^>]+href=['"]([^'"]+)['"][^>]*>([\s\S]*?)</a>""",
    re.IGNORECASE,
)


def classify(title: str) -> str | None:
    """Return election_result, market_print, earnings, or None."""
    blocked = any(word in title for word in BLOCK)
    election = (
        not blocked
        and any(word in title for word in PARTY)
        and any(word in title for word in OUTCOME)
    )
    if election:
        return "election_result"
    if any(word in title for word in MARKET) or (
        "股市" in title and any(word in title for word in MARKET_PLACE)
    ):
        return "market_print"
    if any(word in title for word in EARNINGS) and any(
        word in title for word in EARNINGS_WHERE
    ):
        return "earnings"
    return None


def items_from_html(html: str, source: str) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for match in LINK_RE.finditer(html):
        url = match.group(1).strip()
        title = re.sub(r"<[^>]+>", "", match.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        if not title or not url or url.startswith("#") or url in seen:
            continue
        if url.startswith("/"):
            url = "https://www.news.cn" + url
        kind = classify(title)
        if kind is None:
            continue
        seen.add(url)
        out.append({"source": source, "kind": kind, "title": title, "url": url})
    return out


def fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def collect(limit: int) -> list[dict]:
    rows: list[dict] = []
    for source, url in (("world", WORLD_URL), ("fortune", FORTUNE_URL)):
        try:
            html = fetch(url)
        except Exception as exc:  # noqa: BLE001 — record the list as unavailable
            rows.append({"source": source, "kind": "unmounted", "title": "", "url": url, "error": type(exc).__name__})
            continue
        rows.extend(items_from_html(html, source))
    kept = [row for row in rows if row.get("kind") != "unmounted"]
    notes = [row for row in rows if row.get("kind") == "unmounted"]
    return kept[:limit] + notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List overseas market items")
    parser.add_argument("--html", help="Parse this file instead of fetching")
    parser.add_argument("--source", default="world", help="Source name for --html")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args(argv)
    if args.html:
        html = open(args.html, encoding="utf-8", errors="replace").read()
        rows = items_from_html(html, args.source)[: args.limit]
    else:
        rows = collect(args.limit)
    json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
