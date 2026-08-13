"""
Forex Factory economic calendar — free, NO API KEY
--------------------------------------------------
Forex Factory publishes an (undocumented but widely used) JSON feed of the week's
economic calendar, including the consensus FORECAST, the PREVIOUS value, and an
impact rating. We use it to add "expected / previous" to /macro — the piece FMP's
free tier didn't provide.

No key, no per-request limit. Cached once per day. Degrades gracefully: if the
feed is unreachable or its format changes, /macro just omits the expected line.
"""

import datetime as dt
import requests

# Public mirrors of the calendar feed — this week + next week to cover ~14 days.
_THIS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
         "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"]
_NEXT = ["https://nfs.faireconomy.media/ff_calendar_nextweek.json",
         "https://cdn-nfs.faireconomy.media/ff_calendar_nextweek.json"]
_cache = {"data": None, "day": None}


def _one(urls):
    for url in urls:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                body = r.json()
                if isinstance(body, list) and body:
                    return body
        except Exception:
            continue
    return []


def _fetch():
    today = dt.date.today()
    # Reuse the cache only if it actually has data (don't let a transient empty
    # fetch poison the whole session — retry next time instead).
    if _cache["day"] == today and _cache["data"]:
        return _cache["data"]
    data = _one(_THIS) + _one(_NEXT)
    if data:
        _cache["data"] = data
        _cache["day"] = today
    return data


def us_expected(keywords, on_date):
    """For a US event on `on_date` matching any keyword → {forecast, previous,
    impact}. None if not found. `on_date` is 'YYYY-MM-DD'."""
    for e in _fetch():
        if (e.get("country") or "").upper() != "USD":
            continue
        title = (e.get("title") or "").lower().replace("-", " ")
        if (e.get("date") or "")[:10] == on_date and any(k in title for k in keywords):
            return {"forecast": e.get("forecast"), "previous": e.get("previous"),
                    "impact": e.get("impact")}
    return None


def diagnose():
    data = _fetch()
    us = sum(1 for e in data if (e.get("country") or "").upper() == "USD")
    sample = next((e.get("title") for e in data
                   if (e.get("country") or "").upper() == "USD"), "")
    return f"{len(data)} events, {us} US (e.g. '{sample}')"
