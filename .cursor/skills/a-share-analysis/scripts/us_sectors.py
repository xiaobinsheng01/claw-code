#!/usr/bin/env python3
"""Classify a fixed US sector basket. Does not score and does not order.

Quotes need a last price and a previous close. A percent by itself is not
enough. The basket does not enter W3, the repair day, or the behavior score.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

HOT_PCT = 1.0
COLD_PCT = -1.0
PCT_TOLERANCE = 0.05
QUOTE_URL = "https://qt.gtimg.cn/q="

# group is the existing A-share sleeve this ETF is allowed to sit beside.
# It does not open a new exclusive pair and does not enter W3.
SLEEVES = (
    ("XLK", "科技", "growth_tech", "电子、信息"),
    ("SMH", "半导体", "growth_chip", "科创芯片"),
    ("SOXX", "费城半导体ETF", "growth_chip", "科创芯片"),
    ("XLC", "通信服务", "communication", "通信"),
    ("XLF", "金融", "financial", "银行对照，不进修复"),
    ("XLE", "能源", "energy", "煤炭、能源，不进托住"),
    ("XLB", "材料", "materials", "有色，不升成托"),
    ("XLP", "必需消费", "staples", "食品饮料，不改第二托"),
    ("XLY", "可选消费", "discretionary", "不进互斥"),
    ("XLV", "医疗", "health", "医药，不进互斥"),
    ("XLI", "工业", "industrial", "不进互斥"),
    ("XLU", "公用事业", "utility", "不进托住"),
    ("XLRE", "房地产", "real_estate", "不进互斥"),
)
INDEXES = ("DJI", "INX", "IXIC", "AAPL")


def _pct(last: float, prev: float) -> float:
    return (last - prev) / prev * 100.0


def _round_pct(value: float) -> float:
    return int(value * 100 + (0.5 if value >= 0 else -0.5)) / 100.0


def sleeve_state(last, prev, vendor_pct=None) -> dict:
    """One sleeve. Missing prices and a vendor percent that disagrees are unchecked."""
    if last is None or prev is None or prev <= 0:
        return {"state": "unchecked", "pct": None, "reason": "missing_price"}
    computed = _pct(float(last), float(prev))
    if vendor_pct is not None and abs(float(vendor_pct) - computed) > PCT_TOLERANCE:
        return {
            "state": "unchecked",
            "pct": _round_pct(computed),
            "vendor_pct": float(vendor_pct),
            "reason": "vendor_mismatch",
        }
    if computed >= HOT_PCT:
        state = "hot"
    elif computed <= COLD_PCT:
        state = "cold"
    else:
        state = "middle"
    return {"state": state, "pct": _round_pct(computed), "reason": None}


def _group_word(states: list[str]) -> str:
    if not states or any(item == "unchecked" for item in states):
        return "unchecked"
    if all(item == "hot" for item in states):
        return "hot"
    if all(item == "cold" for item in states):
        return "cold"
    if all(item == "middle" for item in states):
        return "flat"
    return "split"


def classify(quotes: dict) -> dict:
    """quotes[symbol] = {last, prev, vendor_pct?}. Symbols are the basket codes."""
    rows = []
    for symbol, name, group, a_share in SLEEVES:
        quote = quotes.get(symbol) or {}
        got = sleeve_state(quote.get("last"), quote.get("prev"), quote.get("vendor_pct"))
        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "group": group,
                "a_share": a_share,
                "last": quote.get("last"),
                "prev": quote.get("prev"),
                "vendor_pct": quote.get("vendor_pct"),
                **got,
            }
        )
    hot = sorted((row for row in rows if row["state"] == "hot"), key=lambda row: row["pct"], reverse=True)
    cold = sorted((row for row in rows if row["state"] == "cold"), key=lambda row: row["pct"])
    tech = _group_word([row["state"] for row in rows if row["group"] == "growth_tech"])
    chip = _group_word([row["state"] for row in rows if row["group"] == "growth_chip"])
    if tech == "unchecked" or chip == "unchecked":
        growth = "unchecked"
    elif tech == chip:
        growth = tech
    else:
        growth = "split"
    return {
        "rows": rows,
        "hot": [row["symbol"] for row in hot],
        "cold": [row["symbol"] for row in cold],
        "middle": [row["symbol"] for row in rows if row["state"] == "middle"],
        "unchecked": [row["symbol"] for row in rows if row["state"] == "unchecked"],
        "growth_tech": tech,
        "growth_chip": chip,
        "growth": growth,
        "enters_w3": False,
        "enters_repair": False,
        "changes_score": False,
    }


def session_label(dt_et: datetime) -> str:
    """Regular US cash hours are intraday. 16:00 New York or later is the close."""
    if dt_et.weekday() >= 5:
        return "closed_market"
    minutes = dt_et.hour * 60 + dt_et.minute
    if minutes < 9 * 60 + 30:
        return "premarket"
    if minutes < 16 * 60:
        return "intraday"
    return "close"


def _parse_tencent(body: str) -> dict:
    parts = body.split("~")
    ts = next((item for item in parts if len(item) >= 19 and item[4] == "-" and item[13] == ":"), "")
    last = float(parts[3])
    prev = float(parts[4])
    vendor = None
    high = low = None
    if ts:
        idx = parts.index(ts)
        vendor = float(parts[idx + 2])
        high = float(parts[idx + 3])
        low = float(parts[idx + 4])
    et = None
    beijing = None
    if ts:
        et = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("America/New_York"))
        beijing = et.astimezone(ZoneInfo("Asia/Shanghai"))
    return {
        "name": parts[1],
        "code": parts[2],
        "last": last,
        "prev": prev,
        "open": float(parts[5]) if parts[5] else None,
        "vendor_pct": vendor,
        "high": high,
        "low": low,
        "quote_et": None if et is None else et.strftime("%Y-%m-%d %H:%M:%S"),
        "quote_beijing": None if beijing is None else beijing.strftime("%Y-%m-%d %H:%M:%S"),
        "session": None if et is None else session_label(et),
    }


def fetch() -> dict:
    codes = [f"us{symbol}" for symbol, *_rest in SLEEVES] + [f"us{symbol}" for symbol in INDEXES]
    req = urllib.request.Request(QUOTE_URL + ",".join(codes), headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", errors="replace")
    parsed = {}
    for line in raw.split(";"):
        line = line.strip()
        if "=\"" not in line or "none_match" in line:
            continue
        body = line.split("=\"", 1)[1].rstrip("\"")
        if "~" not in body:
            continue
        key = line.split("=", 1)[0]
        if not key.startswith("v_us"):
            continue
        item = _parse_tencent(body)
        parsed[key[4:]] = item
    basket = {symbol: parsed.get(symbol, {}) for symbol, *_rest in SLEEVES}
    tape = classify(basket)
    sessions = {item.get("session") for item in parsed.values() if item.get("session")}
    session = sessions.pop() if len(sessions) == 1 else "unchecked"
    tape["session"] = session
    tape["source"] = "qt.gtimg.cn"
    tape["indexes"] = {symbol: parsed.get(symbol) for symbol in INDEXES}
    tape["quotes"] = {symbol: parsed.get(symbol) for symbol, *_rest in SLEEVES}
    return tape


def self_check() -> int:
    failed = 0

    def expect(name: str, cond: bool, detail) -> None:
        nonlocal failed
        if cond:
            print(f"ok {name}")
        else:
            print(f"FAIL {name}: {detail}")
            failed += 1

    nov4 = classify(
        {
            "XLK": {"last": 178.93, "prev": 176.51, "vendor_pct": 1.37},
            "SMH": {"last": 512.30, "prev": 518.52, "vendor_pct": -1.20},
            "SOXX": {"last": 457.64, "prev": 465.00, "vendor_pct": -1.58},
            "XLC": {"last": 105.01, "prev": 101.44, "vendor_pct": 3.52},
            "XLF": {"last": 58.32, "prev": 57.19, "vendor_pct": 1.98},
            "XLE": {"last": 64.52, "prev": 66.68, "vendor_pct": -3.25},
            "XLB": {"last": 53.29, "prev": 52.59, "vendor_pct": 1.33},
            "XLP": {"last": 91.39, "prev": 88.95, "vendor_pct": 2.74},
            "XLY": {"last": 121.78, "prev": 117.33, "vendor_pct": 3.79},
            "XLV": {"last": 185.49, "prev": 184.02, "vendor_pct": 0.80},
            "XLI": {"last": 168.06, "prev": 171.32, "vendor_pct": -1.90},
            "XLU": {"last": 42.92, "prev": 43.42, "vendor_pct": -1.15},
            "XLRE": {"last": 44.36, "prev": 43.67, "vendor_pct": 1.58},
        }
    )
    expect("nov4 growth splits", nov4["growth"] == "split" and nov4["growth_tech"] == "hot" and nov4["growth_chip"] == "cold", nov4["growth"])
    expect("nov4 hot names", nov4["hot"] == ["XLY", "XLC", "XLP", "XLF", "XLRE", "XLK", "XLB"], nov4["hot"])
    expect("nov4 cold names", nov4["cold"] == ["XLE", "XLI", "SOXX", "SMH", "XLU"], nov4["cold"])
    expect("10:33 snapshot health stays middle", nov4["middle"] == ["XLV"], nov4["middle"])
    expect("nov4 stays out of the score", nov4["enters_w3"] is False and nov4["enters_repair"] is False and nov4["changes_score"] is False, nov4)

    both_hot = classify(
        {
            "XLK": {"last": 110, "prev": 100},
            "SMH": {"last": 110, "prev": 100},
            "SOXX": {"last": 110, "prev": 100},
            "XLF": {"last": 90, "prev": 100},
        }
    )
    expect("agreed chip and tech heat is hot", both_hot["growth"] == "hot", both_hot["growth"])
    expect("financial cold does not enter repair", both_hot["enters_repair"] is False and "XLF" in both_hot["cold"], both_hot["cold"])

    both_cold = classify(
        {
            "XLK": {"last": 98, "prev": 100},
            "SMH": {"last": 98, "prev": 100},
            "SOXX": {"last": 98, "prev": 100},
        }
    )
    expect("agreed drawdown is cold", both_cold["growth"] == "cold", both_cold["growth"])

    missing = classify({"XLK": {"last": 101, "prev": 100}, "SMH": {"last": 90}})
    expect("missing chip price leaves growth unchecked", missing["growth"] == "unchecked" and "SOXX" in missing["unchecked"], missing["unchecked"])

    mismatch = sleeve_state(110, 100, vendor_pct=2.0)
    expect("vendor mismatch is unchecked", mismatch["state"] == "unchecked" and mismatch["reason"] == "vendor_mismatch", mismatch)

    flat = classify(
        {
            "XLK": {"last": 100.5, "prev": 100},
            "SMH": {"last": 100.4, "prev": 100},
            "SOXX": {"last": 99.7, "prev": 100},
        }
    )
    expect("inside one percent is flat", flat["growth"] == "flat", flat["growth"])

    et = datetime(2026, 11, 4, 10, 33, tzinfo=ZoneInfo("America/New_York"))
    expect("wednesday morning is intraday", session_label(et) == "intraday", session_label(et))
    expect("sixteen hundred is the close", session_label(et.replace(hour=16, minute=0)) == "close", session_label(et.replace(hour=16)))
    expect("premarket is not a close", session_label(et.replace(hour=9, minute=0)) == "premarket", session_label(et.replace(hour=9, minute=0)))
    return failed


def main() -> int:
    if "--self-check" in sys.argv:
        failed = self_check()
        return 1 if failed else 0
    print(json.dumps(fetch(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
