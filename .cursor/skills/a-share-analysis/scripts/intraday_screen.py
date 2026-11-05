#!/usr/bin/env python3
"""Screen every drive-sector main-board name above both averages.

K-lines and holder pages stay in this process. Every name that clears both
averages is printed with the two holder cells. Nothing is dropped for length.
A holder page already cached for the same two report dates is not fetched again.
This does not score, does not change the path, and does not place an order.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sector_drive import (  # noqa: E402
    COMPLEX_WORDS,
    INDUSTRIES,
    STATE_WORDS,
    _fetch,
    _norm_name,
    _num,
    fetch_board,
    fetch_closes,
    main_board,
)

RULE = "holder-v1"
CACHE = Path(__file__).resolve().parents[1] / "cache" / "holders.json"
SH_PREFIX = {"000001", "000016", "000300", "000688", "000852", "000685", "000807", "000993", "000941"}


def trade_day() -> str:
    now = datetime.now(timezone(timedelta(hours=8)))
    return now.strftime("%Y-%m-%d")


def assign(members: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Main board only. Semiconductor names stay on the chip board."""
    chip = {row["code"] for row in members.get("pt01801081", []) if main_board(row["code"])}
    taken: set[str] = set()
    out: dict[str, list[dict]] = {}
    for item in INDUSTRIES:
        rows = []
        for row in members.get(item["board"], []):
            code = row["code"]
            if not main_board(code) or code in taken:
                continue
            if item["index"] == "399811" and code in chip:
                continue
            taken.add(code)
            rows.append({**row, "index": item["index"], "industry": item["name"]})
        out[item["index"]] = rows
    return out


def both_averages(closes: list[float], live) -> dict:
    """Live price must clear both averages. Today's close is inside the averages."""
    if len(closes) < 10 or any(px is None or px <= 0 for px in closes[-10:]):
        return {"state": "unchecked"}
    last = closes[-10:]
    ma5 = sum(last[-5:]) / 5.0
    ma10 = sum(last) / 10.0
    px = live if live is not None and live > 0 else last[-1]
    state = "above" if px > ma5 and px > ma10 else "below"
    return {"state": state, "ma5": ma5, "ma10": ma10, "px": px}


def judge_holders(periods: list[dict]) -> dict:
    """Simple structure and a new state-capital holder. One failed cell stays out."""
    if len(periods) < 2 or any(len(item.get("holders") or []) < 10 for item in periods[:2]):
        return {"simple": "未核", "state": "未核", "hits": [], "complex_one": ""}
    current, prior = periods[0], periods[1]
    complex_names = [
        holder["name"]
        for holder in current["holders"]
        if any(word in _norm_name(holder["name"]) for word in COMPLEX_WORDS)
    ]
    simple = "复杂" if complex_names else "简单"
    prior_names = {_norm_name(holder["name"]) for holder in prior["holders"]}
    hits = []
    unchecked = False
    for holder in current["holders"]:
        name = _norm_name(holder["name"])
        if not any(word in name for word in STATE_WORDS):
            continue
        change = str(holder.get("change") or "").strip()
        seen = name in prior_names
        if (not seen) and change == "新进":
            hits.append(
                {
                    "name": holder["name"],
                    "ratio": holder.get("ratio"),
                    "day": current.get("day"),
                    "prev": prior.get("day"),
                }
            )
        elif (not seen and change != "新进") or (seen and change == "新进"):
            unchecked = True
    if unchecked:
        state = "未核"
    elif hits:
        state = "有"
    else:
        state = "无"
    return {
        "simple": simple,
        "state": state,
        "hits": hits,
        "complex_one": complex_names[0] if complex_names else "",
        "days": [current.get("day"), prior.get("day")],
    }


def fetch_holder_periods(code: str) -> list[dict]:
    suffix = "SH" if code.startswith("6") else "SZ"
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_EH_HOLDERS"
        "&columns=END_DATE,HOLDER_RANK,HOLDER_NAME,HOLD_NUM_RATIO,HOLD_NUM_CHANGE"
        f"&filter=(SECUCODE=%22{code}.{suffix}%22)(IS_REPORT=%221%22)"
        "&pageNumber=1&pageSize=40&sortColumns=END_DATE,HOLDER_RANK&sortTypes=-1,1"
    )
    payload = json.loads(_fetch(url).decode())
    data = ((payload.get("result") or {}).get("data")) or []
    periods: list[dict] = []
    current = None
    bucket: list[dict] = []
    for row in data:
        day = str(row.get("END_DATE") or "")[:10]
        if day != current:
            if bucket:
                periods.append({"day": current, "holders": bucket})
            current = day
            bucket = []
        if len(bucket) < 10 and row.get("HOLDER_NAME"):
            bucket.append(
                {
                    "name": row["HOLDER_NAME"],
                    "ratio": row.get("HOLD_NUM_RATIO"),
                    "change": row.get("HOLD_NUM_CHANGE"),
                }
            )
    if bucket:
        periods.append({"day": current, "holders": bucket})
    return periods[:2]


def load_cache() -> dict:
    if not CACHE.exists():
        return {}
    try:
        payload = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if payload.get("rule") != RULE:
        return {}
    return payload.get("names") or {}


def save_cache(names: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"rule": RULE, "names": names}, ensure_ascii=False), encoding="utf-8")


def judge_one(code: str) -> dict:
    try:
        judged = judge_holders(fetch_holder_periods(code))
    except Exception as exc:  # noqa: BLE001 — one failed page stays unchecked
        judged = {"simple": "未核", "state": "未核", "hits": [], "complex_one": "", "error": type(exc).__name__}
    judged["cached"] = False
    return judged


def holder_cell(code: str, cache: dict) -> dict:
    cached = cache.get(code)
    if isinstance(cached, dict) and cached.get("simple"):
        cached["cached"] = True
        return cached
    judged = judge_one(code)
    cache[code] = {key: judged.get(key) for key in ("simple", "state", "hits", "complex_one", "days")}
    return judged


def _prefix(code: str) -> str:
    if code.startswith(("5", "6", "9")) or code in SH_PREFIX:
        return "sh" + code
    return "sz" + code


def fetch_quotes(codes: list[str]) -> dict:
    out = {}
    for start in range(0, len(codes), 60):
        batch = codes[start : start + 60]
        raw = _fetch("https://qt.gtimg.cn/q=" + ",".join(_prefix(code) for code in batch)).decode("gbk", "replace")
        for chunk in raw.split(";"):
            if "~" not in chunk:
                continue
            parts = chunk.split('"', 1)[-1].split("~")
            if len(parts) < 35:
                continue
            out[parts[2]] = {
                "name": parts[1].replace(" ", ""),
                "px": _num(parts[3]),
                "pct": _num(parts[32]),
                "ts": parts[30],
            }
    return out


def _kline(code: str):
    try:
        return code, fetch_closes(code), ""
    except Exception as exc:  # noqa: BLE001
        return code, [], type(exc).__name__


def screen(day: str) -> dict:
    members = {}
    for item in INDUSTRIES:
        members[item["board"]] = fetch_board(item["board"])
    assigned = assign(members)
    flat = [row for rows in assigned.values() for row in rows]
    bars = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(_kline, row["code"]) for row in flat]
        for future in as_completed(futures):
            code, series, _err = future.result()
            bars[code] = series
    quotes = fetch_quotes([row["code"] for row in flat])
    grouped: dict[str, list[dict]] = {item["index"]: [] for item in INDUSTRIES}
    for row in flat:
        series = bars.get(row["code"]) or []
        if not series or series[-1][0] != day:
            continue
        live = (quotes.get(row["code"]) or {}).get("px")
        stats = both_averages([px for _, px in series], live)
        if stats["state"] != "above":
            continue
        quote = quotes.get(row["code"]) or {}
        grouped[row["index"]].append(
            {
                **row,
                "name": quote.get("name") or row.get("name") or "",
                "px": quote.get("px"),
                "pct": quote.get("pct"),
                "ma5": stats["ma5"],
                "ma10": stats["ma10"],
                "ts": quote.get("ts") or "",
            }
        )
    cache = load_cache()
    opened = []
    pending = []
    summary = []
    for item in INDUSTRIES:
        rows = grouped[item["index"]]
        rows.sort(key=lambda row: (row["pct"] is None, -(row["pct"] or -999)))
        keep = [row for row in rows if row["pct"] is not None]
        for row in keep:
            cached = cache.get(row["code"])
            if isinstance(cached, dict) and cached.get("simple"):
                row["holder"] = {**cached, "cached": True}
            else:
                pending.append(row)
            opened.append(row)
        summary.append(
            {
                "index": item["index"],
                "name": item["name"],
                "above": len(rows),
                "opened": len(keep),
                "unchecked": max(0, len(rows) - len(keep)),
            }
        )
    if pending:
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = {pool.submit(judge_one, row["code"]): row for row in pending}
            for future in as_completed(futures):
                row = futures[future]
                judged = future.result()
                row["holder"] = judged
                cache[row["code"]] = {
                    key: judged.get(key) for key in ("simple", "state", "hits", "complex_one", "days")
                }
    save_cache(cache)
    passed = []
    for row in opened:
        cell = row["holder"]
        if cell.get("simple") == "简单" and cell.get("state") == "有":
            passed.append(row)
    return {"summary": summary, "opened": opened, "passed": passed}


def _fmt(value, digits: int) -> str:
    number = _num(value)
    if number is None:
        return "未核"
    return f"{number:.{digits}f}"


def main() -> int:
    payload = screen(trade_day())
    for item in payload["summary"]:
        print(
            f"{item['name']} {item['index']} 站上 {item['above']} 打开 {item['opened']} 本轮未核 {item['unchecked']}"
        )
    print("打开")
    for row in payload["opened"]:
        cell = row["holder"]
        print(
            " ".join(
                [
                    row["industry"],
                    row["code"],
                    row["name"],
                    _fmt(row["px"], 2),
                    _fmt(row["pct"], 2),
                    "5日",
                    _fmt(row["ma5"], 2),
                    "10日",
                    _fmt(row["ma10"], 2),
                    cell.get("simple") or "未核",
                    cell.get("state") or "未核",
                ]
            )
        )
    print("判断结果")
    if not payload["passed"]:
        print("无")
        return 0
    for row in payload["passed"]:
        for hit in row["holder"].get("hits") or []:
            print(
                f"{row['code']} {row['name']} 本期 {hit.get('day')} 上期 {hit.get('prev')} "
                f"{hit.get('name')} {hit.get('ratio')} 新进"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
