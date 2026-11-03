#!/usr/bin/env python3
"""Print the quotes, breadth, and repair-day checks for one A-share close.

Stdlib only. Does not score W1–W8 and does not emit an order.
5-day average turnover is left null when the history endpoint is down.
"""

from __future__ import annotations

import json
import sys
import urllib.request

CODES = [
    "sh000001",
    "sz399001",
    "sz399006",
    "sh000688",
    "sh000016",
    "sh000300",
    "sh000852",
    "sz399986",
    "sz399998",
    "sz399997",
    "sh600519",
    "sz300750",
    "sz002081",
    "sh601061",
    "sz000559",
]


def fetch(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def num(value: str):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def quotes() -> dict:
    raw = fetch("https://qt.gtimg.cn/q=" + ",".join(CODES)).decode("gbk", "replace")
    out = {}
    for chunk in raw.split(";"):
        if "~" not in chunk:
            continue
        parts = chunk.split('"', 1)[-1].split("~")
        if len(parts) < 35:
            continue
        amount_wan = num(parts[37]) if len(parts) > 37 else None
        out[parts[2]] = {
            "name": parts[1].replace(" ", ""),
            "px": num(parts[3]),
            "prev": num(parts[4]),
            "open": num(parts[5]),
            "pct": num(parts[32]),
            "high": num(parts[33]),
            "low": num(parts[34]),
            "amount_yi": None if amount_wan is None else round(amount_wan / 10000, 2),
            "ts": parts[30],
        }
    return out


def breadth() -> dict:
    raw = fetch(
        "https://flash-api.xuangubao.cn/api/market_indicator/latest",
    ).decode()
    data = json.loads(raw).get("data") or {}
    keep = [
        "rise_count",
        "fall_count",
        "stay_count",
        "limit_up_count",
        "limit_down_count",
        "limit_up_broken_count",
        "limit_up_broken_ratio",
        "yesterday_limit_up_avg_pcp",
    ]
    return {key: data.get(key) for key in keep}


def limit_pool(trade_date: str) -> dict:
    url = (
        "https://push2ex.eastmoney.com/getTopicZTPool"
        "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
        f"&Pageindex=0&pagesize=500&sort=fbt:asc&date={trade_date}"
    )
    payload = json.loads(fetch(url).decode()).get("data") or {}
    pool = payload.get("pool") or []
    counts: dict[str, int] = {}
    height = []
    for item in pool:
        industry = item.get("hybk") or "?"
        counts[industry] = counts.get(industry, 0) + 1
        height.append(
            {
                "lbc": item.get("lbc"),
                "name": (item.get("n") or "").replace(" ", ""),
                "code": item.get("c"),
                "hy": industry,
            }
        )
    height.sort(key=lambda row: int(row["lbc"] or 0), reverse=True)
    top = sorted(counts.items(), key=lambda pair: pair[1], reverse=True)[:8]
    return {"n": len(pool), "top_hy": top, "height": height[:8]}


def repair(q: dict) -> dict:
    bank = (q.get("399986") or {}).get("pct")
    coal = (q.get("399998") or {}).get("pct")
    csi300 = (q.get("000300") or {}).get("pct")
    csi1000 = (q.get("000852") or {}).get("pct")
    liquor = (q.get("399997") or {}).get("pct")
    moutai = (q.get("600519") or {}).get("pct")
    catl = (q.get("300750") or {}).get("pct")
    cond1 = (bank is not None and bank < 0) or (coal is not None and coal < 0)
    cond2 = (
        csi300 is not None and csi1000 is not None and csi300 < csi1000
    )
    # 茅台所在行业用中证白酒。宁德个股上涨不等于电力设备行业上涨。
    cond3 = liquor is not None and liquor > 0
    return {
        "bank_pct": bank,
        "coal_pct": coal,
        "csi300_pct": csi300,
        "csi1000_pct": csi1000,
        "liquor_pct": liquor,
        "moutai_pct": moutai,
        "catl_pct": catl,
        "bank_or_coal_down": cond1,
        "csi300_gain_below_csi1000": cond2,
        "liquor_industry_up": cond3,
        "all_three": bool(cond1 and cond2 and cond3),
    }


def sell_checks(q: dict) -> dict:
    jt = q.get("002081") or {}
    zx = q.get("601061") or {}
    wx = q.get("000559") or {}
    return {
        "002081": {
            "open": jt.get("open"),
            "high": jt.get("high"),
            "low": jt.get("low"),
            "px": jt.get("px"),
            "open_at_or_above_3_88": (jt.get("open") or 0) >= 3.88,
            "traded_through_3_88_4_00": (
                jt.get("high") is not None
                and jt.get("low") is not None
                and jt["high"] >= 3.88
                and jt["low"] <= 4.00
            ),
        },
        "601061": {
            "open": zx.get("open"),
            "high": zx.get("high"),
            "px": zx.get("px"),
            "close_below_9_55": zx.get("px") is not None and zx["px"] < 9.55,
            "high_below_9_55": zx.get("high") is not None and zx["high"] < 9.55,
        },
        "000559": {"px": wx.get("px"), "pct": wx.get("pct")},
    }


def main() -> int:
    fast = "--fast" in sys.argv
    packet: dict = {"fast": fast, "gaps": []}
    try:
        q = quotes()
    except Exception as exc:  # noqa: BLE001 — surface the source failure
        print(json.dumps({"error": f"quotes: {type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    packet["quotes"] = q
    missing = [code[2:] for code in CODES if code[2:] not in q]
    if missing:
        packet["gaps"].append("quotes_missing:" + ",".join(missing))
    sh = (q.get("000001") or {}).get("amount_yi")
    sz = (q.get("399001") or {}).get("amount_yi")
    packet["turnover"] = {
        "shanghai_yi": sh,
        "shenzhen_yi": sz,
        "two_market_yi": None if sh is None or sz is None else round(sh + sz, 2),
        "avg5_yi": None,
    }
    packet["gaps"].append("5日成交额未接入，量能不打分")
    if not fast:
        try:
            packet["breadth"] = breadth()
        except Exception as exc:  # noqa: BLE001
            packet["breadth"] = None
            packet["gaps"].append(f"breadth: {type(exc).__name__}")
        trade_date = ""
        for row in q.values():
            ts = row.get("ts") or ""
            if len(ts) >= 8 and ts[:8].isdigit():
                trade_date = ts[:8]
                break
        try:
            packet["limit_up"] = limit_pool(trade_date)
        except Exception as exc:  # noqa: BLE001
            packet["limit_up"] = None
            packet["gaps"].append(f"limit_up: {type(exc).__name__}")
    packet["repair"] = repair(q)
    packet["sell_checks"] = sell_checks(q)
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
