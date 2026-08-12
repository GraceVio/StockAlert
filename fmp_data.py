"""
Financial Modeling Prep (FMP) — cleaner consensus data (free key)
-----------------------------------------------------------------
Gives two things yfinance/FRED can't do well:
  • MACRO expected values — the consensus FORECAST + previous (and actual once
    released) for CPI, Fed, GDP, PCE, jobs, etc.
  • EARNINGS estimates — consensus EPS and revenue for the next report.

Needs a free key set as the FMP_API_KEY secret (https://site.financialmodelingprep.com).
Everything degrades gracefully to the existing sources if the key is missing or a
call fails. Note: FMP's free tier changes over time — if a call returns nothing,
these features simply fall back; nothing breaks.
"""

import os
import datetime as dt
import requests

BASE = "https://financialmodelingprep.com/api/v3"
_econ = {"data": None, "day": None}


def _key():
    return os.environ.get("FMP_API_KEY")


def available() -> bool:
    return bool(_key())


def _get(path, params=None):
    k = _key()
    if not k:
        return None
    p = dict(params or {})
    p["apikey"] = k
    try:
        r = requests.get(f"{BASE}/{path}", params=p, timeout=15)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def economic_calendar():
    """US-relevant economic events for the next ~10 days, cached for the day."""
    today = dt.date.today()
    if _econ["day"] == today and _econ["data"] is not None:
        return _econ["data"]
    data = _get("economic_calendar",
                {"from": today.isoformat(),
                 "to": (today + dt.timedelta(days=10)).isoformat()})
    _econ["data"] = data if isinstance(data, list) else []
    _econ["day"] = today
    return _econ["data"]


def macro_expected(keywords, on_date):
    """Find a US event on `on_date` whose name matches any keyword →
    {estimate, previous, actual} (each may be None). None if not found."""
    if not available():
        return None
    for e in economic_calendar():
        if (e.get("country") or "").upper() not in ("US", "USD"):
            continue
        name = (e.get("event") or "").lower()
        if (e.get("date") or "")[:10] == on_date and any(k in name for k in keywords):
            return {"estimate": e.get("estimate"), "previous": e.get("previous"),
                    "actual": e.get("actual")}
    return None


def earnings_estimate(ticker):
    """Consensus {eps, revenue} for the ticker's next report, or None."""
    if not available():
        return None
    data = _get(f"historical/earning_calendar/{ticker.upper()}")
    if not isinstance(data, list) or not data:
        return None
    today = dt.date.today().isoformat()
    future = sorted((x for x in data if (x.get("date") or "") >= today),
                    key=lambda x: x.get("date", ""))
    if not future:
        return None
    row = future[0]
    return {"eps": row.get("epsEstimated"), "revenue": row.get("revenueEstimated")}
