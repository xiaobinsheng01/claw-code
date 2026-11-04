#!/usr/bin/env python3
"""Print the quotes, breadth, and repair-day checks for one A-share close.

Stdlib only. Does not score W1–W8 and does not emit an order.
favored_path, four_paths, and first_day_follow_through do not score W1–W8 and do not emit an order.
5-day turnover comes from Tencent bars that print amount. Volume is never converted. The average stays null when that bar is missing or does not match the quote.
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
    "sh000807",
    "sh000015",
    "sz399809",
    "sz399811",
    "sh000685",
    "sz399389",
    "sh000941",
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


def _score_high(value: float, bands: tuple[tuple[float, int], ...], low: int) -> int:
    for threshold, score in bands:
        if value >= threshold:
            return score
    return low


def w6_scores(
    breadth_ratio: float | None,
    limit_up: int | None,
    limit_down: int | None,
    broken_ratio: float | None,
    premium: float | None,
    height: int | None,
    volume_ratio: float | None,
) -> dict:
    """Map the five W6 inputs onto -2..+2. Missing inputs are omitted, not filled with 0."""
    dims: dict[str, int] = {}
    if breadth_ratio is not None:
        dims["breadth"] = _score_high(
            breadth_ratio, ((0.80, 2), (0.65, 1), (0.35, 0), (0.20, -1)), -2
        )
    if limit_up is not None and limit_down is not None and broken_ratio is not None:
        if limit_down >= 30 or broken_ratio >= 0.55:
            dims["limit"] = -2
        elif limit_up < 20 or broken_ratio >= 0.40:
            dims["limit"] = -1
        elif limit_up >= 80 and broken_ratio < 0.25 and limit_down <= 15:
            dims["limit"] = 2
        elif limit_up >= 40 and broken_ratio < 0.35:
            dims["limit"] = 1
        else:
            dims["limit"] = 0
    if premium is not None:
        dims["premium"] = _score_high(premium, ((0.03, 2), (0.01, 1), (-0.01, 0), (-0.03, -1)), -2)
    if height is not None:
        dims["height"] = _score_high(float(height), ((6, 2), (4, 1), (2, 0), (1, -1)), -2)
    if volume_ratio is not None:
        dims["volume"] = _score_high(
            volume_ratio, ((1.30, 2), (1.10, 1), (0.90, 0), (0.70, -1)), -2
        )
    scored = list(dims.values())
    composite = None
    if len(scored) >= 4:
        if len(scored) == 5 and sum(item >= 1 for item in scored) >= 3 and all(item > -1 for item in scored):
            composite = 2
        else:
            composite = max(-2, min(2, round(sum(scored) / len(scored))))
    return {"dims": dims, "composite": composite, "scored_n": len(scored)}


def sector_state(
    top_count: int | None,
    second_count: int | None,
    height_in_top_industry: bool | None,
    left_intraday_high: bool | None,
    leader_index_pct: float | None,
    industry_mapped: bool,
    catalyst_dated: bool,
) -> str:
    """Return unchecked, candidate, first_day, or not_established."""
    if top_count is None or second_count is None or height_in_top_industry is None:
        return "unchecked"
    concentrated = top_count >= 15 and top_count >= 1.5 * second_count
    if (
        concentrated
        and height_in_top_industry
        and left_intraday_high is False
        and catalyst_dated
    ):
        return "candidate"
    if (
        concentrated
        and industry_mapped
        and leader_index_pct is not None
        and leader_index_pct >= 3
    ):
        return "first_day"
    return "not_established"


GROWTH_INDUSTRY_KEYS = ("半导体", "元件", "光学", "电子", "通信", "计算机", "软件", "自动化")


def leader_index_pct(top_industry: str, q: dict) -> tuple[bool, float | None]:
    mapped = any(key in top_industry for key in GROWTH_INDUSTRY_KEYS)
    if not mapped:
        return False, None
    if any(key in top_industry for key in ("通信",)):
        return True, (q.get("399389") or {}).get("pct")
    chip = (q.get("000685") or {}).get("pct")
    electronics = (q.get("399811") or {}).get("pct")
    values = [item for item in (chip, electronics) if item is not None]
    if not values:
        return True, None
    return True, max(values)


def four_paths(low: float, high: float) -> dict:
    """Hard band, narrow band, and the two outside lines.

    The narrow ceiling is the midpoint, not the high. This does not score
    W1–W8, does not choose the favored path, and does not emit an order.
    """
    midpoint = (low + high) / 2.0
    return {
        "hard_low": low,
        "hard_high": high,
        "midpoint": midpoint,
        "narrow_low": low,
        "narrow_high": midpoint,
        "above": high,
        "below": low,
    }


def round_fen(value: float) -> float:
    """Half-up to 分 for a positive price.

    Python round is bankers' rounding, so 9.345 would become 9.34.
    A binary float of an exact half-fen can land a hair under the half.
    The extra 1e-6 is on the fen scale and does not move a price a full 0.001 away.
    """
    return int(value * 100 + 0.5 + 1e-6) / 100.0


def favored_path(
    prior_high: float,
    session_high: float | None,
    prev_close_above_older_high: bool,
    w3_negative: bool,
    a50_gap: bool,
) -> str:
    """Choose the favored path. Does not move the narrow ceiling or the W3 sign.

    A session high above the prior high wins. Touching that high does not.
    This does not score W1–W8 and does not emit an order.
    """
    if session_high is not None and session_high > prior_high:
        return "above_prior_high"
    if prev_close_above_older_high:
        return "above_prior_high"
    if w3_negative and a50_gap:
        return "narrow_lower"
    return "not_forced"


def first_day_follow_through(
    open_premium_held: bool | None,
    index_above_prior_high: bool | None,
    leader_above_prior_high: bool | None,
) -> str:
    """Three next-day checks. One False stops the first day. None is unchecked.

    This does not fetch the limit-up list and does not emit an order.
    """
    flags = (open_premium_held, index_above_prior_high, leader_above_prior_high)
    if any(flag is False for flag in flags):
        return "stopped"
    if any(flag is None for flag in flags):
        return "unchecked"
    return "continue"


def leader_hold_level(top_industry: str, q: dict):
    """Prior high of the same index leader_index_pct would pick.

    Telecom uses 399389. Otherwise the higher percent of 000685 and 399811,
    then that index's high. Does not score and does not emit an order.
    """
    if not any(key in top_industry for key in GROWTH_INDUSTRY_KEYS):
        return None
    if "通信" in top_industry:
        return (q.get("399389") or {}).get("high")
    choices = []
    for code in ("000685", "399811"):
        row = q.get(code) or {}
        if row.get("pct") is not None and row.get("high") is not None:
            choices.append((row["pct"], row["high"]))
    if not choices:
        return None
    return max(choices)[1]


def index_amount_wan(code: str) -> list[tuple[str, float]]:
    """Tencent daily bars that include turnover. Field 8 is万元. Volume is not converted."""
    url = (
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        f"?param={code},day,,,8,qfq"
    )
    raw = fetch(url, timeout=20).decode("utf-8", "replace")
    if "=" in raw[:20]:
        raw = raw.split("=", 1)[1]
    rows = json.loads(raw)["data"][code]["day"]
    out = []
    for row in rows:
        if len(row) < 9:
            continue
        out.append((str(row[0]), float(row[8])))
    return out


def turnover_ratio(sh_quote_yi: float | None, sz_quote_yi: float | None) -> dict:
    """Today's two-market amount versus the prior five sessions. Quote must match the bar."""
    empty = {"avg5_yi": None, "ratio": None, "source": None}
    if sh_quote_yi is None or sz_quote_yi is None:
        return empty
    try:
        sh_rows = index_amount_wan("sh000001")
        sz_rows = index_amount_wan("sz399001")
    except Exception as exc:  # noqa: BLE001 — the caller records the gap
        return empty | {"error": f"{type(exc).__name__}: {exc}"}
    if len(sh_rows) < 6 or len(sz_rows) < 6:
        return empty
    sh_today = sh_rows[-1][1] / 10000
    sz_today = sz_rows[-1][1] / 10000
    if abs(sh_today - sh_quote_yi) > 0.05 or abs(sz_today - sz_quote_yi) > 0.05:
        return empty | {"mismatch": [round(sh_today, 2), round(sz_today, 2)]}
    prior = [
        sh_rows[-6 + i][1] / 10000 + sz_rows[-6 + i][1] / 10000
        for i in range(5)
    ]
    avg5 = sum(prior) / 5
    today = sh_quote_yi + sz_quote_yi
    if avg5 <= 0:
        return empty
    return {
        "avg5_yi": round(avg5, 2),
        "today_yi": round(today, 2),
        "ratio": round(today / avg5, 3),
        "source": "proxy.finance.qq.com/newfqkline",
    }


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


def _pct(q: dict, code: str):
    return (q.get(code) or {}).get("pct")


def _direction(value):
    if value is None:
        return None
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def session_path(close: float, day_low: float, day_high: float, prev_close: float) -> dict:
    """Label where a close sits in the next session's four paths.

    The band is the prior day's low and high. This does not score W1–W8
    and does not choose a bank direction.
    """
    midpoint = (day_low + day_high) / 2
    if close < day_low:
        zone = "below_hard"
    elif close <= midpoint:
        zone = "narrow_lower"
    elif close <= day_high:
        zone = "hard_upper"
    else:
        zone = "above_hard"
    if close < prev_close:
        shanghai = "down"
    elif close > prev_close:
        shanghai = "up"
    else:
        shanghai = "flat"
    cushion_applies = shanghai == "down" and zone != "above_hard"
    return {
        "zone": zone,
        "midpoint": midpoint,
        "shanghai_vs_prev": shanghai,
        "cushion_applies": cushion_applies,
    }


def structure(q: dict) -> dict:
    """Daily hedge, cushion, and national-team wording. Does not change the score."""
    shanghai = _pct(q, "000001")
    chinext = _pct(q, "399006")
    sse50 = _pct(q, "000016")
    bank = _pct(q, "399986")
    electronics = _pct(q, "399811")
    chip = _pct(q, "000685")
    telecom = _pct(q, "399389")
    star50 = _pct(q, "000688")
    food = _pct(q, "000807")
    liquor = _pct(q, "399997")
    dividend = _pct(q, "000015")
    insurance = _pct(q, "399809")
    bank_dir = _direction(bank)
    electronics_dir = _direction(electronics)
    if bank_dir in ("up", "down") and electronics_dir in ("up", "down") and bank_dir != electronics_dir:
        hedge = "opposite"
    elif bank_dir is None or electronics_dir is None:
        hedge = None
    else:
        hedge = "same_or_flat"
    if hedge == "opposite" and bank_dir == "up":
        down_side = "growth"
    elif hedge == "opposite" and bank_dir == "down":
        down_side = "banks"
    else:
        down_side = None
    if shanghai is None or bank is None:
        bank_cushion = None
    elif shanghai >= 0:
        bank_cushion = "not_applicable"
    elif bank > 0:
        bank_cushion = "bank_held"
    else:
        bank_cushion = "bank_did_not_hold"
    if bank_cushion == "bank_held":
        book_price = "book_price_held"
    elif bank_cushion == "bank_did_not_hold":
        book_price = "book_price_did_not_hold"
    elif bank_cushion == "not_applicable":
        book_price = "not_applicable"
    else:
        book_price = None
    if shanghai is None or food is None:
        food_cushion = None
    elif shanghai >= 0:
        food_cushion = "not_applicable"
    elif food > 0:
        food_cushion = "food_held"
    else:
        food_cushion = "food_did_not_hold"
    chinext_hard = chinext is not None and chinext <= -1
    secondary = []
    if chinext_hard:
        if food is not None and food > 0:
            secondary.append("food")
        if liquor is not None and liquor > 0:
            secondary.append("liquor")
        if dividend is not None and dividend > 0:
            secondary.append("dividend")
        if insurance is not None and insurance > 0:
            secondary.append("insurance")
    return {
        "shanghai_pct": shanghai,
        "bank_pct": bank,
        "electronics_pct": electronics,
        "chip_pct": chip,
        "telecom_pct": telecom,
        "chinext_pct": chinext,
        "star50_pct": star50,
        "sse50_pct": sse50,
        "food_pct": food,
        "liquor_pct": liquor,
        "dividend_pct": dividend,
        "insurance_pct": insurance,
        "new_energy_pct": _pct(q, "000941"),
        "hedge": hedge,
        "down_side": down_side,
        "bank_cushion": bank_cushion,
        "book_price": book_price,
        "national_team_share_anchor": "2026-09-30_unchanged",
        "national_team_entities": [
            "huijin_investment",
            "huijin_asset_mgmt",
            "csfc",
            "guoxin",
            "chengtong",
        ],
        "national_team_bank_anchor_entities": [
            "huijin_investment",
            "huijin_asset_mgmt",
            "csfc",
        ],
        "operating_platform_share_as_of": "2026-09-30_top10",
        "operating_platform_changes_open": False,
        "national_team_excluded": ["guojin_fund"],
        "huijin_brokers_not_cushion": ["000166", "601066", "601995", "601336"],
        "etf_creation_label": "broad_etf_creation_not_huijin_buy",
        "food_cushion": food_cushion,
        "chinext_down_at_least_1pct": chinext_hard,
        "secondary_cushion": secondary,
        "weights_fell_less": sse50 is not None and chinext is not None and sse50 > chinext,
        "decision": index_decision(
            {
                "hedge": hedge,
                "down_side": down_side,
                "bank_cushion": bank_cushion,
                "shanghai_pct": shanghai,
                "electronics_pct": electronics,
                "telecom_pct": telecom,
            }
        ),
        "hedge_leg": "bank_399986_vs_electronics_399811",
        "primary_cushion": "bank",
        "second_cushion_on_shanghai_down": "food",
        "sample_window": "2026-07-01_to_2026-11-03",
        "not_cushions": [
            "coal",
            "energy",
            "oil_gas",
            "brokers",
            "nonferrous",
            "new_energy",
            "insurance",
            "power",
            "transport",
            "pharma",
            "nonbank",
            "media",
            "software",
            "appliance",
        ],
        "interval_gain_not_cushion": [
            "liquor",
            "dividend",
            "coal",
            "energy",
            "oil_gas",
            "insurance",
            "power",
            "transport",
            "pharma",
            "nonbank",
            "appliance",
        ],
        "appliance_index": "gap_tencent_sina_eastmoney_public_2026-11-04",
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


def _q(pcts: dict) -> dict:
    return {code: {"pct": value} for code, value in pcts.items()}


def index_decision(s: dict) -> str | None:
    """One close sentence from the hedge and cushion flags. Does not change the score."""
    hedge = s.get("hedge")
    shanghai = s.get("shanghai_pct")
    if hedge is None or shanghai is None:
        return None
    electronics = s.get("electronics_pct")
    telecom = s.get("telecom_pct")
    growth_up = (electronics is not None and electronics > 0) or (
        telecom is not None and telecom > 0
    )
    rally = shanghai > 0 and growth_up
    if hedge == "opposite" and s.get("down_side") == "growth" and s.get("bank_cushion") == "bank_held":
        return "growth_down_bank_held"
    if hedge == "opposite" and s.get("down_side") == "banks" and rally:
        return "growth_led_rebound"
    if hedge == "same_or_flat" and s.get("bank_cushion") == "bank_did_not_hold":
        return "same_direction_bank_not_held"
    if rally:
        return "rally_without_hedge"
    return "no_set_decision"


def month_stage(q: dict) -> dict:
    """Classify a finished month. Inputs are month percent changes, not daily moves.

    Does not change the score, and is not called on the daily quote packet.
    Bleed still needs a separate count of how many industries closed down.
    """
    sse = _pct(q, "000001")
    sse50 = _pct(q, "000016")
    chinext = _pct(q, "399006")
    bank = _pct(q, "399986")
    electronics = _pct(q, "399811")
    sessions = q.get("_sessions")
    missing = any(value is None for value in (sse, sse50, chinext, bank, electronics))
    if missing:
        return {
            "stage": None,
            "support": None,
            "rebound": None,
            "drift": None,
            "too_short": None,
        }
    too_short = sessions is not None and sessions < 5
    less = sse50 - chinext
    support = (not too_short) and sse < 0 and chinext < 0 and less >= 10 and bank > 0
    rebound = (not too_short) and sse > 0 and electronics > 0
    drift = (
        (not too_short)
        and sse < 0
        and bank > 0
        and electronics < 0
        and electronics > -15
        and less < 10
    )
    if too_short:
        stage = "too_short"
    elif rebound:
        stage = "rebound"
    elif support:
        stage = "support"
    elif drift:
        stage = "drift"
    else:
        stage = "unclassified"
    return {
        "stage": stage,
        "support": support,
        "rebound": rebound,
        "drift": drift,
        "too_short": too_short,
        "sse50_less_than_chinext_pp": round(less, 2),
    }


def self_check() -> int:
    """Offline checks for the national-team wording flag. No network."""
    cases = [
        (
            "nov3 bank did not hold",
            _q({"000001": -0.82, "399986": -0.39, "399811": -5.83, "000807": 1.93}),
            "book_price_did_not_hold",
            "bank_did_not_hold",
        ),
        (
            "shanghai down bank up",
            _q({"000001": -1.0, "399986": 0.5, "399811": -2.0, "000807": -0.2}),
            "book_price_held",
            "bank_held",
        ),
        (
            "shanghai up",
            _q({"000001": 0.2, "399986": 0.5, "399811": -0.4, "000807": 0.1}),
            "not_applicable",
            "not_applicable",
        ),
        (
            "bank missing",
            _q({"000001": -0.5, "399811": -1.0}),
            None,
            None,
        ),
    ]
    failed = 0
    for name, quotes, book, bank in cases:
        got = structure(quotes)
        if got["book_price"] != book or got["bank_cushion"] != bank:
            print(f"FAIL {name}: {got['book_price']} {got['bank_cushion']}")
            failed += 1
        elif got["national_team_share_anchor"] != "2026-09-30_unchanged":
            print(f"FAIL {name}: anchor {got['national_team_share_anchor']}")
            failed += 1
        elif "guojin_fund" not in got["national_team_excluded"]:
            print(f"FAIL {name}: guojin not excluded")
            failed += 1
        elif got["national_team_entities"][-2:] != ["guoxin", "chengtong"]:
            print(f"FAIL {name}: operating platforms {got['national_team_entities']}")
            failed += 1
        elif got["national_team_bank_anchor_entities"] != [
            "huijin_investment",
            "huijin_asset_mgmt",
            "csfc",
        ]:
            print(f"FAIL {name}: bank anchor entities")
            failed += 1
        elif got["operating_platform_changes_open"] is not False:
            print(f"FAIL {name}: operating platform opened a position")
            failed += 1
        elif got["operating_platform_share_as_of"] != "2026-09-30_top10":
            print(f"FAIL {name}: platform as-of {got['operating_platform_share_as_of']}")
            failed += 1
        else:
            print(f"ok {name}")
        decision = got["decision"]
        expect = {
            "nov3 bank did not hold": "same_direction_bank_not_held",
            "shanghai down bank up": "growth_down_bank_held",
            "shanghai up": "no_set_decision",
            "bank missing": None,
        }[name]
        if decision != expect:
            print(f"FAIL {name} decision: {decision}")
            failed += 1
    rebound = structure(
        _q({"000001": 1.0, "399986": -0.2, "399811": 1.2, "399389": 0.8, "000807": -0.3})
    )
    if rebound["decision"] != "growth_led_rebound" or rebound["down_side"] != "banks":
        print(f"FAIL rebound decision: {rebound['decision']} {rebound['down_side']}")
        failed += 1
    else:
        print("ok growth led rebound")
    registered = structure(
        _q(
            {
                "000001": -1.2,
                "399006": -1.4,
                "399986": -0.3,
                "399811": -2.0,
                "000807": 0.2,
                "399997": -0.4,
                "000015": 0.1,
                "399809": 0.6,
            }
        )
    )
    if registered["secondary_cushion"] != ["food", "dividend", "insurance"]:
        print(f"FAIL secondary: {registered['secondary_cushion']}")
        failed += 1
    elif registered["food_cushion"] != "food_held" or registered["bank_cushion"] != "bank_did_not_hold":
        print("FAIL cushions moved")
        failed += 1
    elif "insurance" not in registered["not_cushions"]:
        print("FAIL insurance still a cushion")
        failed += 1
    else:
        print("ok secondary insurance")
    quiet = structure(
        _q(
            {
                "000001": -0.2,
                "399006": -0.5,
                "399986": 0.1,
                "399811": -0.2,
                "399809": 1.0,
            }
        )
    )
    if quiet["secondary_cushion"] != []:
        print(f"FAIL quiet secondary: {quiet['secondary_cushion']}")
        failed += 1
    else:
        print("ok chinext under 1pct skips insurance")
    months = [
        (
            "july support",
            {"000001": -6.4, "000016": -2.2, "399006": -23.0, "399986": 11.8, "399811": -34.5, "_sessions": 23},
            "support",
        ),
        (
            "august rebound",
            {"000001": 4.0, "000016": 0.5, "399006": 2.8, "399986": 0.1, "399811": 10.1, "_sessions": 21},
            "rebound",
        ),
        (
            "september drift",
            {"000001": -3.6, "000016": -3.9, "399006": -8.8, "399986": 2.4, "399811": -9.8, "_sessions": 21},
            "drift",
        ),
        (
            "october support",
            {"000001": -6.3, "000016": -2.9, "399006": -23.0, "399986": 4.8, "399811": -28.1, "_sessions": 17},
            "support",
        ),
        (
            "november too short",
            {"000001": 0.25, "000016": 0.95, "399006": -0.91, "399986": -1.29, "399811": -4.84, "_sessions": 2},
            "too_short",
        ),
    ]
    for name, raw, stage in months:
        got = month_stage(_q(raw) | {"_sessions": raw["_sessions"]})
        if got["stage"] != stage:
            print(f"FAIL {name}: {got['stage']}")
            failed += 1
        else:
            print(f"ok {name}")
    if month_stage(_q({"000001": -3.6, "000016": -3.9, "399006": -8.8, "399986": 2.4}))["stage"] is not None:
        print("FAIL month stage without electronics")
        failed += 1
    else:
        print("ok month stage missing electronics")
    paths = [
        (3608.10, "hard_upper", "flat", False),
        (3605.0, "hard_upper", "down", True),
        (3590.0, "narrow_lower", "down", True),
        (3620.0, "hard_upper", "up", False),
        (3630.0, "above_hard", "up", False),
        (3570.0, "below_hard", "down", True),
    ]
    for close, zone, direction, cushion in paths:
        got = session_path(close, 3580.38, 3624.58, 3608.10)
        if (
            got["zone"] != zone
            or got["shanghai_vs_prev"] != direction
            or got["cushion_applies"] is not cushion
        ):
            print(f"FAIL session path {close}: {got}")
            failed += 1
        else:
            print(f"ok session path {zone} {close}")
    nov4 = w6_scores(4331 / 5095, 119, 11, 0.2171, -0.0014, 3, 1.221)
    if nov4["composite"] != 2 or nov4["dims"]["breadth"] != 2 or nov4["dims"]["volume"] != 1:
        print(f"FAIL nov4 w6: {nov4}")
        failed += 1
    else:
        print("ok nov4 w6 +2")
    diluted = w6_scores(0.85, 10, 5, 0.50, 0.0, 3, 1.40)
    if diluted["composite"] != 1 or diluted["dims"]["limit"] != -1:
        print(f"FAIL diluted w6: {diluted}")
        failed += 1
    else:
        print("ok negative dimension blocks +2")
    if w6_scores(0.90, None, None, None, None, None, None)["composite"] is not None:
        print("FAIL w6 scored from one dimension")
        failed += 1
    else:
        print("ok w6 needs four dimensions")
    if sector_state(19, 11, False, True, 12.61, True, False) != "first_day":
        print("FAIL first day")
        failed += 1
    else:
        print("ok first day")
    if sector_state(48, 6, False, True, None, False, False) != "not_established":
        print("FAIL unmapped sector")
        failed += 1
    else:
        print("ok unmapped stays not_established")
    if sector_state(20, 4, True, False, 4.0, True, True) != "candidate":
        print("FAIL candidate")
        failed += 1
    else:
        print("ok candidate")
    if sector_state(19, 11, None, True, 12.0, True, False) != "unchecked":
        print("FAIL missing height")
        failed += 1
    else:
        print("ok missing height stays unchecked")
    morning = favored_path(3624.58, 3605.39, False, True, True)
    if morning != "narrow_lower":
        print(f"FAIL nov4 morning path: {morning}")
        failed += 1
    else:
        print("ok nov4 morning stays narrow")
    broken = favored_path(3624.58, 3686.17, False, True, True)
    if broken != "above_prior_high":
        print(f"FAIL breakout path: {broken}")
        failed += 1
    else:
        print("ok breakout leaves the narrow band")
    premarket = favored_path(3686.17, None, True, True, True)
    if premarket != "above_prior_high":
        print(f"FAIL nov5 premarket path: {premarket}")
        failed += 1
    else:
        print("ok nov5 premarket stays above the prior high")
    touched = favored_path(3624.58, 3624.58, False, True, True)
    if touched != "narrow_lower":
        print(f"FAIL touch does not switch: {touched}")
        failed += 1
    else:
        print("ok touching the prior high does not switch")
    band = four_paths(3590.80, 3686.17)
    if band["narrow_high"] != band["midpoint"] or band["narrow_high"] == band["hard_high"]:
        print(f"FAIL narrow ceiling: {band}")
        failed += 1
    elif round_fen(band["midpoint"]) != 3638.49:
        print(f"FAIL index midpoint fen: {band['midpoint']}")
        failed += 1
    else:
        print("ok index four paths")
    mantis = four_paths(4.12, 4.30)
    if mantis["midpoint"] != 4.21 or round_fen(mantis["midpoint"]) != 4.21:
        print(f"FAIL mantis midpoint: {mantis}")
        failed += 1
    else:
        print("ok mantis midpoint")
    fen_cases = (
        (9.345, 9.35),
        ((9.08 + 9.61) / 2, 9.35),
        (2.325, 2.33),
        ((2.30 + 2.35) / 2, 2.33),
        (12.245, 12.25),
        ((11.79 + 12.70) / 2, 12.25),
        (4.505, 4.51),
        ((4.35 + 4.66) / 2, 4.51),
        (9.344, 9.34),
    )
    for raw, expected in fen_cases:
        got = round_fen(raw)
        if got != expected:
            print(f"FAIL round_fen {raw}: {got}")
            failed += 1
        else:
            print(f"ok round_fen {expected}")
    follow_cases = (
        ((True, True, True), "continue"),
        ((True, False, True), "stopped"),
        ((True, None, True), "unchecked"),
        ((False, None, None), "stopped"),
    )
    for flags, expected in follow_cases:
        got = first_day_follow_through(*flags)
        if got != expected:
            print(f"FAIL follow through {flags}: {got}")
            failed += 1
        else:
            print(f"ok follow through {expected}")
    chip_level = leader_hold_level(
        "半导体",
        {
            "000685": {"pct": 12.61, "high": 2874.16},
            "399811": {"pct": 10.91, "high": 5283.62},
        },
    )
    if chip_level != 2874.16:
        print(f"FAIL chip hold level: {chip_level}")
        failed += 1
    else:
        print("ok chip hold level is the higher percent high")
    if leader_hold_level("银行", {"000685": {"pct": 12.61, "high": 2874.16}}) is not None:
        print("FAIL unmapped hold level")
        failed += 1
    else:
        print("ok unmapped industry has no hold level")
    return 1 if failed else 0


def main() -> int:
    if "--self-check" in sys.argv:
        return self_check()
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
    two = None if sh is None or sz is None else round(sh + sz, 2)
    ratio = turnover_ratio(sh, sz)
    packet["turnover"] = {
        "shanghai_yi": sh,
        "shenzhen_yi": sz,
        "two_market_yi": two,
        "avg5_yi": ratio.get("avg5_yi"),
        "ratio": ratio.get("ratio"),
        "source": ratio.get("source"),
    }
    if ratio.get("avg5_yi") is None:
        packet["gaps"].append("5日成交额未接入，量能不打分")
        if ratio.get("error"):
            packet["gaps"].append("turnover:" + ratio["error"])
        if ratio.get("mismatch"):
            packet["gaps"].append("turnover_mismatch:" + ",".join(str(item) for item in ratio["mismatch"]))
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
    packet["structure"] = structure(q)
    packet["sell_checks"] = sell_checks(q)
    breadth_row = packet.get("breadth") or {}
    rise = breadth_row.get("rise_count")
    fall = breadth_row.get("fall_count")
    breadth_ratio = None
    if rise is not None and fall is not None and rise + fall > 0:
        breadth_ratio = rise / (rise + fall)
    height_n = None
    top_count = second_count = None
    height_in = None
    top_name = ""
    pool = packet.get("limit_up") or {}
    top_hy = pool.get("top_hy") or []
    height_rows = pool.get("height") or []
    if top_hy:
        top_name, top_count = top_hy[0]
        second_count = top_hy[1][1] if len(top_hy) > 1 else 0
    if height_rows and height_rows[0].get("lbc") is not None:
        height_n = int(height_rows[0]["lbc"])
        height_in = height_rows[0].get("hy") == top_name if top_name else None
    elec = q.get("399811") or {}
    left_high = None
    if elec.get("px") is not None and elec.get("high") is not None:
        left_high = elec["px"] < elec["high"]
    mapped, leader = leader_index_pct(top_name, q) if top_name else (False, None)
    packet["w6"] = w6_scores(
        breadth_ratio,
        breadth_row.get("limit_up_count"),
        breadth_row.get("limit_down_count"),
        breadth_row.get("limit_up_broken_ratio"),
        breadth_row.get("yesterday_limit_up_avg_pcp"),
        height_n,
        ratio.get("ratio"),
    )
    sh_bar = q.get("000001") or {}
    if sh_bar.get("low") is not None and sh_bar.get("high") is not None:
        # Tomorrow's band only. Do not choose favored_path from today's high.
        packet["next_session_band"] = four_paths(sh_bar["low"], sh_bar["high"])
    packet["sector_state"] = {
        "state": sector_state(
            top_count,
            second_count,
            height_in,
            left_high,
            leader,
            mapped,
            False,
        ),
        "top_industry": top_name,
        "second_count": second_count,
        "height_in_leader": height_in,
        "leader_index_pct": leader,
        "leader_prior_high": leader_hold_level(top_name, q) if top_name else None,
        "catalyst_dated": False,
        "follow_through": "unchecked",
    }
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
