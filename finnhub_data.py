"""
Finnhub — free-tier data (needs a free FINNHUB_API_KEY secret)
--------------------------------------------------------------
Finnhub's free tier (60 req/min) gives us genuinely COMPANY-SPECIFIC news for
US / North-American stocks — cleaner and more relevant than Yahoo's per-ticker
feed, which mixes in peers and sector stories. We use it for /news + /score, and
expose a couple of extra endpoints (analyst price target, recommendation) for
optional context.

Everything degrades gracefully: no key, a non-US symbol, or any error → returns
None/[] and the caller falls back to the free yfinance data. No SDK needed
(plain HTTPS via requests), so no extra dependency.
"""

import os
import datetime as dt
import requests

_BASE = "https://finnhub.io/api/v1"


def _key():
    return os.environ.get("FINNHUB_API_KEY")


def has_key() -> bool:
    return bool(_key())


def _is_us(ticker: str) -> bool:
    """Finnhub's free company data covers US / North-American symbols, which on
    Yahoo carry NO exchange suffix (EU names look like SAP.DE, ADYEN.AS, …)."""
    return bool(ticker) and "." not in ticker and "^" not in ticker


def _get(path: str, params: dict):
    key = _key()
    if not key:
        return None
    try:
        p = dict(params); p["token"] = key
        r = requests.get(_BASE + path, params=p, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


def company_news(ticker: str, days: int = 3, limit: int = 8, max_age_hours=None):
    """Recent company-specific headlines (US/NA only) via /company-news, newest
    first. Returns [{headline, summary, source, url, dt}] or [] when
    unavailable (no key / non-US / error).

    `max_age_hours` (e.g. 24) drops anything older than that, using each item's
    own publish time — so only genuinely fresh news is shown."""
    if not _is_us(ticker):
        return []
    today = dt.date.today()
    frm = (today - dt.timedelta(days=days)).isoformat()
    data = _get("/company-news", {"symbol": ticker.upper(),
                                  "from": frm, "to": today.isoformat()})
    if not isinstance(data, list):
        return []
    cutoff = None
    if max_age_hours:
        cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - max_age_hours * 3600
    out = []
    for n in data:
        h = n.get("headline")
        if not h:
            continue
        ts = n.get("datetime")
        if cutoff is not None and (not ts or ts < cutoff):
            continue
        out.append({"headline": h, "summary": n.get("summary") or "",
                    "source": n.get("source") or "", "url": n.get("url") or "",
                    "dt": ts})
        if len(out) >= limit:
            break
    return out


def price_target(ticker: str):
    """Analyst price targets via /stock/price-target: {median, high, low, mean,
    n}. None if unavailable. (US/NA only.)"""
    if not _is_us(ticker):
        return None
    d = _get("/stock/price-target", {"symbol": ticker.upper()})
    if not isinstance(d, dict):
        return None
    med = d.get("targetMedian") or d.get("targetMean")
    if not med:
        return None
    return {"median": d.get("targetMedian"), "high": d.get("targetHigh"),
            "low": d.get("targetLow"), "mean": d.get("targetMean"),
            "n": d.get("numberAnalysts")}


def recommendation(ticker: str):
    """Latest analyst consensus via /stock/recommendation:
    {label, buy, hold, sell, period}. None if unavailable. (US/NA only.)"""
    if not _is_us(ticker):
        return None
    d = _get("/stock/recommendation", {"symbol": ticker.upper()})
    if not isinstance(d, list) or not d:
        return None
    r = d[0]                                    # most recent month
    strong_buy = r.get("strongBuy", 0) or 0
    buy = (r.get("buy", 0) or 0) + strong_buy
    hold = r.get("hold", 0) or 0
    sell = (r.get("sell", 0) or 0) + (r.get("strongSell", 0) or 0)
    label = max((("Buy", buy), ("Hold", hold), ("Sell", sell)),
                key=lambda x: x[1])[0]
    return {"label": label, "buy": buy, "hold": hold, "sell": sell,
            "period": r.get("period")}


def diagnose() -> str:
    if not has_key():
        return "no FINNHUB_API_KEY set"
    n = company_news("AAPL", days=5, limit=3)
    return f"key OK — AAPL test returned {len(n)} company headlines"


# --------------------------------------------------------------- insider buys
# WHY ONLY BUYS, AND ONLY CODE "P":
# Corporate insiders (CEO, CFO, directors) must file Form 4 with the SEC within
# 2 BUSINESS DAYS of trading their own stock. That 2-day lag is what makes this
# usable — congressional disclosures run 45 days late and 13F filings up to four
# months, by which time the move is long over.
#
# Most Form 4 rows are noise: option exercises (M), share grants (A) and tax
# withholding (F) happen on a schedule and say nothing about conviction. Only
# transaction code "P" — an open-market purchase with the insider's own money —
# carries any signal. Sales (S) are near-worthless: executives sell constantly
# for taxes, diversification and pre-scheduled 10b5-1 plans.
#
# The research finding worth acting on is CLUSTER buying: several different
# insiders buying within a few weeks, especially the CEO/CFO. One director
# buying a token amount is not a signal.
_BUY_CODE = "P"

# Job titles that matter most. Finnhub's `name` field sometimes carries a role,
# but it is inconsistent, so this is a soft bonus, never a filter.
_SENIOR = ("chief executive", "ceo", "chief financial", "cfo", "president",
           "chairman", "chief operating", "coo")


def insider_buys(ticker: str, days: int = 90):
    """Open-market insider PURCHASES (Form 4 code "P") in the last `days`.

    Returns a summary dict, or None when unavailable (no key / non-US / error):

        {"buyers": 3,            # distinct people who bought
         "trades": 4,            # number of purchase filings
         "shares": 21500,        # total shares bought
         "value": 1830000.0,     # approximate euros/dollars committed
         "senior": True,         # a CEO/CFO/chairman was among the buyers
         "last": "2026-08-14",   # most recent purchase date
         "days": 90,
         "names": ["Sasan Goodarzi", ...]}

    `value` is approximate: Finnhub reports transactionPrice per filing, which
    is the average execution price the insider disclosed.
    """
    if not _is_us(ticker):
        return None
    today = dt.date.today()
    frm = (today - dt.timedelta(days=days)).isoformat()
    d = _get("/stock/insider-transactions",
             {"symbol": ticker.upper(), "from": frm, "to": today.isoformat()})
    if not isinstance(d, dict):
        return None
    rows = d.get("data")
    if not isinstance(rows, list):
        return None

    buyers, trades, shares, value, senior, last = {}, 0, 0, 0.0, False, None
    for r in rows:
        code = (r.get("transactionCode") or "").strip().upper()
        # `change` is the signed share count; a purchase must be positive. Both
        # checks together guard against odd rows where the code is right but
        # the row is actually a disposal.
        chg = r.get("change") or 0
        if code != _BUY_CODE or chg <= 0:
            continue
        name = (r.get("name") or "").strip()
        date = r.get("transactionDate") or r.get("filingDate") or ""
        px = r.get("transactionPrice") or 0
        trades += 1
        shares += int(chg)
        if px:
            value += float(chg) * float(px)
        if name:
            buyers[name] = buyers.get(name, 0) + int(chg)
        if any(k in name.lower() for k in _SENIOR):
            senior = True
        if date and (last is None or date > last):
            last = date

    if not trades:
        return {"buyers": 0, "trades": 0, "shares": 0, "value": 0.0,
                "senior": False, "last": None, "days": days, "names": []}
    return {"buyers": len(buyers), "trades": trades, "shares": shares,
            "value": value, "senior": senior, "last": last, "days": days,
            "names": sorted(buyers, key=buyers.get, reverse=True)[:4]}


def insider_signal(ticker: str, days: int = 90):
    """insider_buys() boiled down to a score + a plain-English line.

    strength 0-3:
      3 = CLUSTER (3+ different insiders bought) — the documented edge
      2 = two insiders, or one senior insider (CEO/CFO/chairman)
      1 = a single non-senior insider bought
      0 = no open-market buying

    Returns {"strength": int, "text": str, "raw": {...}} or None.
    """
    b = insider_buys(ticker, days=days)
    if b is None:
        return None
    n = b["buyers"]
    if n == 0:
        return {"strength": 0, "text": "No insider buying in the last "
                f"{days} days.", "raw": b}
    if n >= 3:
        strength = 3
    elif n == 2 or b["senior"]:
        strength = 2
    else:
        strength = 1
    who = ", ".join(b["names"][:3])
    money = f"~${b['value']:,.0f}" if b["value"] else f"{b['shares']:,} shares"
    lead = ("🟢 CLUSTER BUY" if strength == 3 else
            ("🟢 Insider buying" if strength == 2 else "Insider buying"))
    text = (f"{lead} — {n} insider{'s' if n > 1 else ''} bought {money} "
            f"of their own stock (last {b['last']}). {who}.")
    return {"strength": strength, "text": text, "raw": b}


def diagnose_insider() -> str:
    if not has_key():
        return "no FINNHUB_API_KEY set"
    b = insider_buys("NVDA", days=180)
    if b is None:
        return ("insider endpoint returned nothing — your Finnhub plan may not "
                "include /stock/insider-transactions")
    return (f"insider endpoint OK — NVDA 180d: {b['trades']} open-market "
            f"purchases by {b['buyers']} insiders")
