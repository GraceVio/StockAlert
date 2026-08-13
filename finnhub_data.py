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
