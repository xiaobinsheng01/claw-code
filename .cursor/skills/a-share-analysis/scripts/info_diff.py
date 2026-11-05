#!/usr/bin/env python3
"""Diff official list pages against the ledger. Print only what this window might open.

The ledger stays on disk. A known address is not printed again. A title with no
clock is not marked for opening. This does not score, does not write the ledger,
and does not place an order.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

LINK_RE = re.compile(r"""<a[^>]+href=['"]([^'"]+)['"][^>]*>([\s\S]*?)</a>""", re.I)
URL_RE = re.compile(r"https?://[^\s|）<>\"']+")
CLOCK_RE = re.compile(r"(20\d{2}-\d{2}-\d{2}[ T]\d{2}:\d{2}|\d{2}-\d{2}\s+\d{2}:\d{2})")
DATE_RE = re.compile(r"20\d{2}[-/]\d{2}[-/]\d{2}")
SECTOR_WORDS = ("电子", "芯片", "通信", "银行", "食品饮料", "煤炭")
COMMENT_WORDS = ("访谈", "专访", "快评", "评论", "策略", "投资机构", "CEO", "基金经理", "首席", "综述", "券商")
GENERIC_STEMS = ("电子", "芯片", "通信", "银行", "煤炭", "食品", "中国", "市场", "公司", "指数")
COLLECT_START = "2026-11-03 09:00"

PAGES = (
    ("新华网财经滚动", "https://www.news.cn/fortune/gundong/index.html", "https://www.news.cn"),
    ("新华网国际", "https://www.news.cn/world/index.html", "https://www.news.cn"),
    ("中证网", "https://www.cs.com.cn/", "https://www.cs.com.cn"),
    ("发改委新闻发布", "https://www.ndrc.gov.cn/xwdt/xwfb/", "https://www.ndrc.gov.cn"),
    ("发改委通知", "https://www.ndrc.gov.cn/xxgk/zcfb/tz/", "https://www.ndrc.gov.cn"),
)


def _clean_title(raw: str) -> str:
    title = re.sub(r"<[^>]+>", "", raw)
    return re.sub(r"\s+", " ", title).strip()


def _abs_url(href: str, base: str) -> str:
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return base.rstrip("/") + href
    return base.rstrip("/") + "/" + href.lstrip("./")


def _id_in_text(url: str, text: str) -> bool:
    found = re.findall(r"[0-9a-f]{20,}|detail_\d+|t20\d{6}_\d+", url)
    return any(item and item in text for item in found)


def _stem_seen(title: str, ledger: str) -> bool:
    """The first four-character stem is enough when the ledger already named that story."""
    found = re.search(r"[\u4e00-\u9fff]{4,}", title)
    if not found:
        return False
    stem = found.group(0)[:4]
    if stem in GENERIC_STEMS:
        return False
    return stem in ledger


def _clock_text(clock: str, url: str) -> str:
    found = re.search(r"(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2})", clock)
    if found:
        return f"{found.group(1)} {found.group(2)}"
    found = re.search(r"(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", clock)
    if not found:
        return ""
    year = re.search(r"/(20\d{2})/", url)
    prefix = year.group(1) if year else "2026"
    return f"{prefix}-{found.group(1)}-{found.group(2)} {found.group(3)}:{found.group(4)}"


def classify_link(
    title: str,
    url: str,
    nearby: str,
    ledger: str,
    cursor: str = "",
    start: str = COLLECT_START,
) -> str | None:
    """Return a status, or None when the address is already recorded."""
    if not title or len(title) < 4:
        return None
    if url in ledger or _id_in_text(url, ledger):
        return None
    if any(word in title for word in COMMENT_WORDS):
        return "不进W1–W5"
    if title and (title in ledger or _stem_seen(title, ledger)):
        return "同一事实已记"
    clock = CLOCK_RE.search(nearby)
    if clock:
        stamped = _clock_text(clock.group(1), url)
        if stamped and stamped < start:
            return None
        if stamped and cursor and stamped <= cursor:
            return "缺口"
        return "待打开"
    if DATE_RE.search(nearby) or DATE_RE.search(title):
        return "无发布时间"
    return "无发布时间"


def links_from_html(html: str, base: str, ledger: str, source: str, cursor: str = "") -> list[dict]:
    rows = []
    seen: set[str] = set()
    for match in LINK_RE.finditer(html):
        url = _abs_url(match.group(1), base)
        title = _clean_title(match.group(2))
        if url in seen or "weibo.com" in url or url.startswith("javascript"):
            continue
        if not any(token in url for token in ("/2026", "detail_", "t2026")):
            continue
        prefix = html[max(0, match.start() - 80) : match.start()]
        if "</a>" in prefix:
            prefix = prefix.rsplit("</a>", 1)[-1]
        nearby = prefix + " " + title
        status = classify_link(title, url, nearby, ledger, cursor=cursor)
        if status is None:
            continue
        seen.add(url)
        clock = CLOCK_RE.search(nearby)
        rows.append(
            {
                "source": source,
                "title": title[:48],
                "url": url,
                "status": status,
                "clock": clock.group(1) if clock else "",
                "sector": any(word in title for word in SECTOR_WORDS),
            }
        )
    return rows


def pack(rows: list[dict], open_limit: int = 20) -> dict:
    """Keep every clocked new page up to the cap. Date-only titles stay a short list."""
    def newer(row: dict) -> str:
        return row.get("clock") or ""

    opening = sorted((row for row in rows if row["status"] == "待打开"), key=newer, reverse=True)
    gap = sorted((row for row in rows if row["status"] == "缺口"), key=newer, reverse=True)
    same = [row for row in rows if row["status"] == "同一事实已记"]
    undated = [row for row in rows if row["status"] == "无发布时间"]
    sector_undated = [row for row in undated if row.get("sector")]
    return {
        "open": opening[:open_limit],
        "open_left": max(0, len(opening) - open_limit),
        "gap": gap[:open_limit],
        "gap_left": max(0, len(gap) - open_limit),
        "same_fact": same[:8],
        "comment_skipped": sum(1 for row in rows if row["status"] == "不进W1–W5"),
        "undated_count": len(undated),
        "undated_sector": sector_undated[:8],
    }


def _fetch(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(400000).decode("utf-8", "replace")


def _cursor(ledger: str) -> str:
    found = re.search(r"截止:\s*(20\d{2}-\d{2}-\d{2} \d{2}:\d{2})", ledger)
    return found.group(1) if found else ""


def main() -> int:
    ledger_path = Path(__file__).resolve().parents[1] / "info-ledger.md"
    ledger = ledger_path.read_text(encoding="utf-8") if ledger_path.exists() else ""
    cursor = _cursor(ledger)
    rows: list[dict] = []
    mounts = []
    for source, url, base in PAGES:
        try:
            html = _fetch(url)
        except Exception as exc:  # noqa: BLE001 — a dead list is a mount note, not a story
            mounts.append({"source": source, "url": url, "error": type(exc).__name__})
            continue
        rows.extend(links_from_html(html, base, ledger, source, cursor=cursor))
    overseas = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from overseas_feed import collect

        for item in collect(8):
            url = item.get("url") or ""
            if item.get("kind") == "unmounted":
                mounts.append(item)
                continue
            title = item.get("title") or ""
            if any(word in title for word in COMMENT_WORDS):
                continue
            if url and url not in ledger and not _id_in_text(url, ledger):
                overseas.append(item)
    except Exception as exc:  # noqa: BLE001
        mounts.append({"source": "overseas_feed", "error": type(exc).__name__})
    payload = pack(rows)
    payload["overseas_new"] = overseas
    payload["mounts"] = mounts
    payload["cursor"] = cursor
    payload["empty"] = not payload["open"] and not payload["gap"] and not overseas
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
