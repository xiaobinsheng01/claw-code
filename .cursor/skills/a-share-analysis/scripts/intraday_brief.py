#!/usr/bin/env python3
"""One quote batch for the written path and the written sector groups.

Prints the Shanghai segment and whether bank and electronics point opposite ways.
Does not screen constituents, does not read the ledger, does not score, and does
not place an order.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from close_inputs import favored_path  # noqa: E402
from sector_drive import _fetch, _num  # noqa: E402

BASKET = (
    "000001",
    "399006",
    "000688",
    "000852",
    "399986",
    "399811",
    "000685",
    "399389",
    "000993",
    "000807",
    "399998",
)

HELD = (
    ("002081", "金螳螂", 4.12, 4.30),
    ("601061", "中信金属", 9.08, 9.61),
    ("600127", "金健米业", 9.60, 10.64),
    ("601868", "中国能建", 2.30, 2.35),
    ("000559", "万向钱潮", 11.79, 12.70),
    ("000725", "京东方A", 4.35, 4.66),
    ("600330", "天通股份", 14.74, 16.96),
)
SH_PREFIX = {"000001", "000016", "000300", "000688", "000852", "000685", "000807", "000993", "000941"}


def prefix(code: str) -> str:
    if code.startswith(("5", "6", "9")) or code in SH_PREFIX:
        return "sh" + code
    return "sz" + code


def segment(px: float, low: float, high: float) -> str:
    mid = (low + high) / 2.0
    if px > high:
        return "昨日高点之上"
    if px > mid:
        return "中点到昨日高点"
    if px >= low:
        return "昨日低点到中点"
    return "昨日低点之下"


def pair(bank, electronics) -> str:
    if bank is None or electronics is None:
        return "未核"
    if bank == 0 or electronics == 0 or (bank > 0) == (electronics > 0):
        return "同向或平盘"
    return "相反"


def path_text(prior_high: float, session_high, above_older: bool, w3_negative: bool, a50_gap: bool) -> str:
    kind = favored_path(prior_high, session_high, above_older, w3_negative, a50_gap)
    if kind == "above_prior_high":
        return f"收在{prior_high:.2f}之上"
    if kind == "narrow_lower":
        return "窄区间"
    return "未改主路径"


def live_quotes(codes: list[str]) -> dict:
    raw = _fetch("https://qt.gtimg.cn/q=" + ",".join(prefix(code) for code in codes)).decode("gbk", "replace")
    out = {}
    for chunk in raw.split(";"):
        if "~" not in chunk:
            continue
        parts = chunk.split('"', 1)[-1].split("~")
        if len(parts) < 35:
            continue
        out[parts[2]] = {
            "name": parts[1].replace(" ", ""),
            "px": _num(parts[3]),
            "open": _num(parts[5]),
            "pct": _num(parts[32]),
            "high": _num(parts[33]),
            "low": _num(parts[34]),
            "ts": parts[30],
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Compact intraday path quotes")
    parser.add_argument("--low", type=float, required=True)
    parser.add_argument("--high", type=float, required=True)
    parser.add_argument("--prev-close-above-older", action="store_true")
    parser.add_argument("--w3-negative", action="store_true")
    parser.add_argument("--a50-gap", action="store_true")
    args = parser.parse_args()
    basket = live_quotes(list(BASKET))
    held = live_quotes([code for code, *_ in HELD])
    sh = basket.get("000001") or {}
    px, high, low = sh.get("px"), sh.get("high"), sh.get("low")
    bank = (basket.get("399986") or {}).get("pct")
    elec = (basket.get("399811") or {}).get("pct")
    print(f"上证 {px} 涨跌 {sh.get('pct')} 高 {high} 低 {low} 开 {sh.get('open')} 时点 {sh.get('ts')}")
    if px is not None:
        print(f"落点 {segment(px, args.low, args.high)} 中点 {(args.low + args.high) / 2:.2f}")
    print(f"主路径 {path_text(args.high, high, args.prev_close_above_older, args.w3_negative, args.a50_gap)}")
    print(f"银行与电子 {pair(bank, elec)}")
    for code in ("399986", "399811", "000685", "000993", "399389", "000688", "399006", "000852", "000807", "399998"):
        row = basket.get(code) or {}
        print(f"{code} {row.get('name')} {row.get('px')} {row.get('pct')} 高 {row.get('high')} 低 {row.get('low')}")
    print("持仓")
    for code, _name, hard_low, hard_high in HELD:
        row = held.get(code) or {}
        place = ""
        if row.get("px") is not None:
            place = segment(row["px"], hard_low, hard_high)
        crossed = ""
        if row.get("high") is not None and row["high"] > hard_high:
            crossed = f" 高点越过{hard_high}"
        print(f"{code} {row.get('name')} {row.get('px')} {row.get('pct')} 高 {row.get('high')} 低 {row.get('low')} {place}{crossed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
