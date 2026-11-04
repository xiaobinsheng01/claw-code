#!/usr/bin/env python3
"""Name the industries that lead the favored path, then screen main-board names.

The two columns are marks. They do not score, do not change the path, and do
not place an order. Missing prices stay unchecked and are not treated as a pass.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

EXCESS_PP = 1.0
CAP_YI = 800.0
NEAR = 0.03
MAX_NAMES = 10
MAIN_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")
COMPLEX_WORDS = (
    "有限合伙",
    "合伙企业",
    "员工持股",
    "私募",
    "资管计划",
    "资产管理计划",
    "信托",
    "专户",
)

# Stock boards are Tencent level-1 / level-2 industries. 全指信息 is the parent
# index; its names come from 计算机 after the child industries have taken theirs.
INDUSTRIES = (
    {
        "index": "000685",
        "name": "科创芯片",
        "board": "pt01801081",
        "board_name": "半导体",
        "us": "chip",
    },
    {
        "index": "399811",
        "name": "CSSW电子",
        "board": "pt01801080",
        "board_name": "电子",
        "us": "tech",
    },
    {
        "index": "399389",
        "name": "国证通信",
        "board": "pt01801770",
        "board_name": "通信",
        "us": "communication",
    },
    {
        "index": "000993",
        "name": "全指信息",
        "board": "pt01801750",
        "board_name": "计算机",
        "us": "info",
    },
)
BROAD_INDEXES = frozenset({"000001", "399001", "399006", "000688", "000852", "000300", "000016"})
GROWTH_PATHS = frozenset({"above_prior_high", "upper_half_up"})


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm_name(name: str) -> str:
    return name.replace(" ", "").replace("\u3000", "").replace("（", "(").replace("）", ")")


def main_board(code: str) -> bool:
    digits = "".join(ch for ch in code if ch.isdigit())
    return len(digits) >= 3 and digits[:3] in MAIN_PREFIXES


def cap_ok(cap_yi) -> str:
    value = _num(cap_yi)
    if value is None or value <= 0:
        return "unchecked"
    if value < CAP_YI:
        return "pass"
    return "fail"


def near_ma(closes: list[float]) -> dict:
    """Either the 5-day or the 10-day average may sit within 3%. Both are not required."""
    if len(closes) < 10 or any(px is None or px <= 0 for px in closes[-10:]):
        return {"state": "unchecked", "ma5": None, "ma10": None, "gap": None}
    last = closes[-10:]
    ma5 = sum(last[-5:]) / 5.0
    ma10 = sum(last) / 10.0
    dist5 = abs(last[-1] / ma5 - 1.0)
    dist10 = abs(last[-1] / ma10 - 1.0)
    gap = min(dist5, dist10)
    state = "pass" if dist5 <= NEAR or dist10 <= NEAR else "fail"
    return {
        "state": state,
        "ma5": ma5,
        "ma10": ma10,
        "dist5": dist5,
        "dist10": dist10,
        "gap": gap,
        "close": last[-1],
    }


STATE_WORDS = (
    "社保基金",
    "基本养老保险基金",
    "中央汇金",
    "中国证券金融",
    "中国国新",
    "国新投资",
    "国新宏盛",
    "国新建源",
    "中国诚通",
    "国家集成电路产业投资基金",
    "国有资产监督管理委员会",
    "国资委",
    "国有资本",
    "国有资产经营",
    "国资经营",
)


def _holder_name(item) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "")
    return str(item)


def _holder_ratio(item):
    if isinstance(item, dict):
        return item.get("ratio")
    return None


def _complex_names(names: list) -> list[str]:
    found = []
    for item in list(names)[:10]:
        name = _holder_name(item)
        if any(word in _norm_name(name) for word in COMPLEX_WORDS):
            found.append(name)
    return found


def holder_simple(periods: list) -> str:
    """Both periods need ten names. A complex word fails only the latest period."""
    if len(periods) < 2 or any(len(names) < 10 for names in periods[:2]):
        return "unchecked"
    if _complex_names(periods[0]):
        return "complex"
    return "simple"


def prior_complex(periods: list) -> list[str]:
    """Names in the previous top ten. They are printed and do not remove the stock."""
    if len(periods) < 2:
        return []
    return _complex_names(periods[1])


def state_capital(names: list) -> dict:
    """Listed words count. A state-asset name outside that list is written beside."""
    listed = []
    beside = []
    for item in list(names)[:10]:
        name = _holder_name(item)
        record = {"name": name, "ratio": _holder_ratio(item)}
        norm = _norm_name(name)
        if any(word in norm for word in STATE_WORDS):
            listed.append(record)
        elif "国有资产" in norm or "国资" in norm:
            beside.append(record)
    return {"listed": listed, "beside": beside}


def prior_pct(series: list[tuple[str, float]], trade_date: str):
    """Last bar versus the bar before it. A bar that is not the session stays blank."""
    if len(series) < 2 or series[-1][0] != trade_date:
        return None
    prev = series[-2][1]
    last = series[-1][1]
    if prev is None or prev <= 0 or last is None:
        return None
    return last / prev - 1.0


def earnings_state(profit, profit_yoy, income_yoy) -> str:
    """Latest periodic report. Positive profit and neither line shrinking."""
    profit = _num(profit)
    profit_yoy = _num(profit_yoy)
    income_yoy = _num(income_yoy)
    if profit is None or profit_yoy is None or income_yoy is None:
        return "unchecked"
    if profit > 0 and profit_yoy >= 0 and income_yoy >= 0:
        return "pass"
    return "fail"


def drive_sectors(path: str, pcts: dict, shanghai_pct) -> list[dict]:
    """Industries on the rising side of the favored path that beat Shanghai by 1pp."""
    shanghai = _num(shanghai_pct)
    if path not in GROWTH_PATHS or shanghai is None:
        return []
    picked = []
    for item in INDUSTRIES:
        code = item["index"]
        if code in BROAD_INDEXES:
            continue
        pct = _num(pcts.get(code))
        if pct is None:
            picked.append({**item, "state": "unchecked", "pct": None, "excess": None})
            continue
        excess = pct - shanghai
        if pct > 0 and excess >= EXCESS_PP:
            picked.append({**item, "state": "drive", "pct": pct, "excess": excess})
    picked.sort(key=lambda row: (row["state"] != "drive", -(row["excess"] or -999)))
    return picked


def us_note(item: dict, growth: str | None, hot: set[str], cold: set[str], session: str) -> str:
    """US tape annotates. It does not delete an A-share industry that already qualified."""
    if session != "close":
        pending = "外围待收盘"
    else:
        pending = "外围按现金收盘"
    kind = item["us"]
    if kind == "chip":
        if growth == "split" or "SMH" in cold or "SOXX" in cold:
            return pending + "，芯片外围不确认"
        if growth == "hot" and "SMH" in hot and "SOXX" in hot:
            return pending + "，芯片外围同向"
        return pending + "，芯片外围未核"
    if kind == "communication":
        if "XLC" in hot:
            return pending + "，通信外围对照热"
        if "XLC" in cold:
            return pending + "，通信外围对照冷"
        return pending + "，通信外围未核"
    if kind == "tech":
        return pending + "，不把科技 ETF 写成电子确认"
    return pending + "，信息没有单独的外围 ETF"


def assign_names(sectors: list[dict], members: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Child boards keep their own names. Later boards drop codes already taken."""
    taken: set[str] = set()
    out: dict[str, list[dict]] = {}
    for item in sectors:
        if item["state"] != "drive":
            continue
        rows = []
        for row in members.get(item["board"], []):
            code = row["code"]
            if code in taken or not main_board(code):
                continue
            if cap_ok(row.get("cap_yi")) != "pass":
                continue
            taken.add(code)
            rows.append(row)
        out[item["index"]] = rows
    return out


def recommend(rows: list[dict], held: set[str]) -> dict:
    """Pass every gate, then keep the ten closest to a moving average."""
    passed = []
    held_aside = []
    for row in rows:
        if row.get("near") != "pass":
            continue
        if row.get("holder") != "simple":
            continue
        if row.get("earnings") != "pass":
            continue
        if row["code"] in held:
            held_aside.append(row)
            continue
        passed.append(row)
    passed.sort(key=lambda row: (row.get("gap") is None, row.get("gap") or 1))
    return {"names": passed[:MAX_NAMES], "extra": max(0, len(passed) - MAX_NAMES), "held_aside": held_aside}


def _fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def fetch_board(board: str) -> list[dict]:
    rows = []
    offset = 0
    total = None
    while total is None or offset < total:
        url = (
            "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
            f"?board_code={board}&sort_type=price&direct=down&offset={offset}&count=80"
        )
        payload = json.loads(_fetch(url).decode())["data"]
        total = int(payload["total"])
        batch = payload.get("rank_list") or []
        if not batch:
            break
        for item in batch:
            code = "".join(ch for ch in item["code"] if ch.isdigit())
            rows.append(
                {
                    "code": code,
                    "name": item.get("name") or "",
                    "cap_yi": _num(item.get("zsz")),
                    "px": _num(item.get("zxj")),
                }
            )
        offset += len(batch)
        if offset > 5000:
            break
    return rows


def fetch_closes(code: str) -> list[tuple[str, float]]:
    prefix = "sh" if code.startswith("6") else "sz"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,30,qfq"
    payload = json.loads(_fetch(url).decode())
    series = (payload.get("data") or {}).get(f"{prefix}{code}") or {}
    bars = series.get("qfqday") or series.get("day") or []
    out = []
    for bar in bars:
        if len(bar) < 3:
            continue
        close = _num(bar[2])
        if close is None:
            continue
        out.append((bar[0], close))
    return out


def fetch_holders(code: str) -> list[list[str]]:
    suffix = "SH" if code.startswith("6") else "SZ"
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_EH_HOLDERS"
        "&columns=END_DATE,HOLDER_RANK,HOLDER_NAME,HOLD_NUM_RATIO"
        f"&filter=(SECUCODE=%22{code}.{suffix}%22)(IS_REPORT=%221%22)"
        "&pageNumber=1&pageSize=40&sortColumns=END_DATE,HOLDER_RANK&sortTypes=-1,1"
    )
    payload = json.loads(_fetch(url).decode())
    data = ((payload.get("result") or {}).get("data")) or []
    periods: list[list[str]] = []
    current = None
    bucket: list[str] = []
    for row in data:
        day = str(row.get("END_DATE") or "")[:10]
        if day != current:
            if bucket:
                periods.append(bucket)
            current = day
            bucket = []
        if len(bucket) < 10 and row.get("HOLDER_NAME"):
            bucket.append({"name": row["HOLDER_NAME"], "ratio": row.get("HOLD_NUM_RATIO")})
    if bucket:
        periods.append(bucket)
    return periods[:2]


def fetch_earnings(code: str) -> dict:
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_FINANCE_GINCOME"
        "&columns=REPORT_DATE,PARENT_NETPROFIT,PARENT_NETPROFIT_YOY,OPERATE_INCOME_YOY"
        f"&filter=(SECURITY_CODE=%22{code}%22)"
        "&pageNumber=1&pageSize=1&sortColumns=REPORT_DATE&sortTypes=-1"
    )
    payload = json.loads(_fetch(url).decode())
    data = ((payload.get("result") or {}).get("data")) or []
    if not data:
        return {"earnings": "unchecked"}
    row = data[0]
    return {
        "earnings": earnings_state(
            row.get("PARENT_NETPROFIT"),
            row.get("PARENT_NETPROFIT_YOY"),
            row.get("OPERATE_INCOME_YOY"),
        ),
        "report": str(row.get("REPORT_DATE") or "")[:10],
        "profit": row.get("PARENT_NETPROFIT"),
        "profit_yoy": row.get("PARENT_NETPROFIT_YOY"),
        "income_yoy": row.get("OPERATE_INCOME_YOY"),
    }


def screen(path: str, pcts: dict, shanghai_pct, held: set[str], us: dict, trade_date: str) -> dict:
    sectors = drive_sectors(path, pcts, shanghai_pct)
    members: dict[str, list[dict]] = {}
    for item in sectors:
        if item["state"] != "drive":
            continue
        if item["board"] not in members:
            members[item["board"]] = fetch_board(item["board"])
    # 电子 contains 半导体. Pull the child board even when assigning the parent.
    if any(item["index"] == "399811" and item["state"] == "drive" for item in sectors):
        if "pt01801081" not in members:
            members["pt01801081"] = fetch_board("pt01801081")
    assigned = assign_names(sectors, _without_child(members, sectors))
    flat = []
    for item in sectors:
        if item["state"] != "drive":
            continue
        for row in assigned.get(item["index"], []):
            flat.append({**row, "index": item["index"], "industry": item["name"]})
    closes = _map_fetch(flat, lambda row: fetch_closes(row["code"]))
    kept = []
    for row in flat:
        series = closes.get(row["code"]) or []
        if not series or series[-1][0] != trade_date:
            row["near"] = "unchecked"
            kept.append(row)
            continue
        stats = near_ma([px for _, px in series])
        row["near"] = stats["state"]
        row["gap"] = stats["gap"]
        row["ma5"] = stats["ma5"]
        row["ma10"] = stats["ma10"]
        row["close"] = stats["close"]
        row["pct"] = prior_pct(series, trade_date)
        if stats["state"] == "pass":
            kept.append(row)
    near_rows = [row for row in kept if row.get("near") == "pass"]
    holder_map = _map_fetch(near_rows, lambda row: fetch_holders(row["code"]))
    earn_map = _map_fetch(near_rows, lambda row: fetch_earnings(row["code"]))
    for row in near_rows:
        periods = holder_map.get(row["code"]) or []
        row["holder"] = holder_simple(periods)
        row["prior_complex"] = prior_complex(periods)
        row["state_capital"] = state_capital(periods[0] if periods else [])
        earned = earn_map.get(row["code"]) or {"earnings": "unchecked"}
        row.update(earned)
    picked = recommend(near_rows, held)
    noted = []
    for item in sectors:
        if item["state"] != "drive":
            noted.append(item)
            continue
        noted.append({**item, "us_note": us_note(item, us.get("growth"), set(us.get("hot") or []), set(us.get("cold") or []), us.get("session") or "pending")})
    return {"sectors": noted, "assigned_counts": {key: len(value) for key, value in assigned.items()}, "near_count": len(near_rows), **picked}


def _without_child(members: dict[str, list[dict]], sectors: list[dict]) -> dict[str, list[dict]]:
    """Drop semiconductor codes from the electronics board before assignment."""
    semi = {row["code"] for row in members.get("pt01801081", [])}
    out = {}
    for item in sectors:
        rows = list(members.get(item["board"], []))
        if item["index"] == "399811":
            rows = [row for row in rows if row["code"] not in semi]
        out[item["board"]] = rows
    return out


def _map_fetch(rows: list[dict], fn) -> dict:
    out = {}
    if not rows:
        return out
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fn, row): row["code"] for row in rows}
        for future in as_completed(futures):
            code = futures[future]
            try:
                out[code] = future.result()
            except Exception:
                out[code] = None
    return out


def self_check() -> int:
    failed = 0

    def check(label, cond):
        nonlocal failed
        if not cond:
            failed += 1
            print("FAIL", label)

    pcts = {"000685": 12.61, "399811": 10.91, "399389": 5.29, "000993": 8.81, "399998": -0.31}
    drive = drive_sectors("above_prior_high", pcts, 2.16)
    codes = [row["index"] for row in drive if row["state"] == "drive"]
    check("four industries", codes == ["000685", "399811", "000993", "399389"])
    check("narrow empty", drive_sectors("narrow_lower", pcts, 2.16) == [])
    check("one point short stays out", not any(
        row["index"] == "399389" and row["state"] == "drive"
        for row in drive_sectors("above_prior_high", {"399389": 3.15}, 2.16)
    ))
    check("missing stays unchecked", drive_sectors("upper_half_up", {}, 2.16)[0]["state"] == "unchecked")
    check("main board", main_board("002371") and main_board("600330") and not main_board("300750") and not main_board("688981"))
    check("cap", cap_ok(799.99) == "pass" and cap_ok(800) == "fail" and cap_ok(None) == "unchecked")
    closes = [10] * 9 + [10.2]
    near = near_ma(closes)
    check("near either line", near["state"] == "pass" and near["dist5"] <= NEAR)
    far = near_ma([10] * 9 + [11])
    check("far from both", far["state"] == "fail")
    check("short history", near_ma([10] * 9)["state"] == "unchecked")
    simple = holder_simple([["甲公司"] * 10, ["乙公司"] * 10])
    check("simple", simple == "simple")
    check("partnership", holder_simple([["某有限合伙"] + ["甲"] * 9, ["乙"] * 10]) == "complex")
    check("short holders", holder_simple([["甲"] * 9, ["乙"] * 10]) == "unchecked")
    prior = [["甲公司"] * 10, ["天津信月潭企业管理合伙企业(有限合伙)", "某信托计划"] + ["乙"] * 8]
    check("prior complex stays", holder_simple(prior) == "simple")
    check("prior complex named", "有限合伙" in prior_complex(prior)[0] and len(prior_complex(prior)) == 2)
    social = state_capital(["全国社保基金五零四组合", "甲公司"])
    check(
        "social fund listed",
        social["listed"] == [{"name": "全国社保基金五零四组合", "ratio": None}] and social["beside"] == [],
    )
    local = state_capital([{"name": "山东省国有资产投资控股有限公司", "ratio": 4.27}])
    check(
        "local holding stays beside",
        local["listed"] == []
        and local["beside"] == [{"name": "山东省国有资产投资控股有限公司", "ratio": 4.27}],
    )
    funds = state_capital([
        {"name": "华芯投资管理有限责任公司-国家集成电路产业投资基金二期股份有限公司", "ratio": 3.72},
        "国家集成电路产业投资基金股份有限公司",
    ])
    check("both chip funds listed", len(funds["listed"]) == 2 and funds["beside"] == [] and funds["listed"][0]["ratio"] == 3.72)
    sasac = state_capital(["某某市国有资产监督管理委员会"])
    check("sasac is listed", sasac["listed"] and sasac["beside"] == [])
    check("plain holder", state_capital(["甲公司"]) == {"listed": [], "beside": []})
    moved = prior_pct([("2026-11-03", 12.42), ("2026-11-04", 13.36)], "2026-11-04")
    check("prior pct", moved is not None and abs(moved - (13.36 / 12.42 - 1)) < 1e-12)
    check("pct other day", prior_pct([("2026-11-03", 12.42), ("2026-11-04", 13.36)], "2026-11-05") is None)
    check("earnings pass", earnings_state(1, 0, 0) == "pass")
    check("earnings loss", earnings_state(-1, 10, 10) == "fail")
    check("earnings gap", earnings_state(1, None, 1) == "unchecked")
    note = us_note(INDUSTRIES[0], "split", {"XLK", "XLC"}, {"SMH", "SOXX", "XLE"}, "intraday")
    check("split keeps a note", "不确认" in note and "待收盘" in note)
    members = {
        "pt01801081": [{"code": "002371", "cap_yi": 100}, {"code": "688981", "cap_yi": 100}],
        "pt01801080": [{"code": "002371", "cap_yi": 100}, {"code": "000100", "cap_yi": 900}, {"code": "002415", "cap_yi": 200}],
        "pt01801770": [{"code": "600498", "cap_yi": 150}],
        "pt01801750": [{"code": "002371", "cap_yi": 100}, {"code": "600570", "cap_yi": 300}],
    }
    assigned = assign_names(drive, _without_child(members, drive))
    check("chip keeps main board", [row["code"] for row in assigned["000685"]] == ["002371"])
    check("electronics drops the chip and the large cap", [row["code"] for row in assigned["399811"]] == ["002415"])
    check("info drops a code already taken", [row["code"] for row in assigned["000993"]] == ["600570"])
    sample = [
        {"code": "600001", "near": "pass", "holder": "simple", "earnings": "pass", "gap": 0.02},
        {"code": "600002", "near": "pass", "holder": "simple", "earnings": "pass", "gap": 0.01},
        {"code": "002081", "near": "pass", "holder": "simple", "earnings": "pass", "gap": 0.001},
        {"code": "600003", "near": "pass", "holder": "complex", "earnings": "pass", "gap": 0.0},
    ]
    picked = recommend(sample, {"002081"})
    check("closest first and holding aside", [row["code"] for row in picked["names"]] == ["600002", "600001"])
    check("held aside", [row["code"] for row in picked["held_aside"]] == ["002081"])
    return failed


def main() -> int:
    if "--self-check" in sys.argv:
        failed = self_check()
        print("failed", failed)
        return 1 if failed else 0
    trade_date = "2026-11-04"
    if "--trade-date" in sys.argv:
        trade_date = sys.argv[sys.argv.index("--trade-date") + 1]
    held = set()
    if "--held" in sys.argv:
        held = {item for item in sys.argv[sys.argv.index("--held") + 1].split(",") if item}
    us = {"growth": "split", "hot": [], "cold": [], "session": "pending"}
    if "--us-json" in sys.argv:
        us = json.loads(sys.argv[sys.argv.index("--us-json") + 1])
    result = screen("above_prior_high", {
        "000685": 12.61,
        "399811": 10.91,
        "399389": 5.29,
        "000993": 8.81,
    }, 2.16, held, us, trade_date)
    json.dump(result, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
