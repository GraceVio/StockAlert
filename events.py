"""
Event alerts — earnings calls + market-moving US macro events
-------------------------------------------------------------
Two feeds, both delivered to Telegram:

  • EARNINGS  — automatic. Scans your watchlist via yfinance and lists which
                names report in the next few days (gap risk around reports).

  • MACRO     — CPI (inflation), FOMC rate decisions, Jobs (NFP), GDP, PCE.
                These are the releases that move the whole market. yfinance
                has no clean macro calendar, so we keep a small JSON file
                (macro_calendar.json) you can edit. A built-in fallback list
                is used if the file is missing. Jobs-report Fridays (first
                Friday of the month) are added automatically.

IMPORTANT: macro dates are BEST-EFFORT — verify/update from the official
schedules (links printed by `python events.py --sources`):
  BLS  (CPI/PPI/Jobs/Retail): https://www.bls.gov/schedule/news_release/
  Fed  (FOMC rate decision):  https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
  BEA  (GDP/PCE):             https://www.bea.gov/news/schedule

Run:  python events.py            # send today's digest to Telegram
      python events.py --print    # just print it
"""

import os
import sys
import json
import datetime as dt
from zoneinfo import ZoneInfo
import scanner as s

MACRO_FILE      = "macro_calendar.json"
EARN_LOOKAHEAD  = 7    # list earnings within this many days
MACRO_LOOKAHEAD = 7    # list macro events within this many days

# Built-in fallback macro calendar (times are CET). BEST-EFFORT — edit
# macro_calendar.json to override with official dates. name / date / cet.
DEFAULT_MACRO = [
    {"date": "2026-08-13", "cet": "14:30", "name": "🇺🇸 US CPI (Inflation), July"},
    {"date": "2026-08-27", "cet": "14:30", "name": "🇺🇸 US GDP (2nd est.), Q2"},
    {"date": "2026-08-29", "cet": "14:30", "name": "🇺🇸 US PCE (Fed's inflation gauge)"},
    {"date": "2026-09-16", "cet": "14:30", "name": "🇺🇸 US CPI (Inflation), Aug"},
    {"date": "2026-09-17", "cet": "20:00", "name": "🏦 FOMC rate decision"},
    {"date": "2026-10-28", "cet": "19:00", "name": "🏦 FOMC rate decision"},
    {"date": "2026-12-09", "cet": "20:00", "name": "🏦 FOMC rate decision"},
]


def _today():
    return dt.datetime.now(ZoneInfo("Europe/Berlin")).date()


# ---------------------------------------------------------------- macro
def _nfp_fridays(months_ahead=3):
    """First Friday of each month = US jobs report (NFP), 14:30 CET."""
    out = []
    d = _today().replace(day=1)
    for _ in range(months_ahead + 1):
        # find first Friday of month d
        first = d.replace(day=1)
        offset = (4 - first.weekday()) % 7      # Friday = weekday 4
        friday = first + dt.timedelta(days=offset)
        out.append({"date": friday.isoformat(), "cet": "14:30",
                    "name": "🇺🇸 US Jobs report (NFP)"})
        # next month
        d = (first.replace(day=28) + dt.timedelta(days=7)).replace(day=1)
    return out


def load_macro():
    """Macro events from macro_calendar.json (if present) else the fallback,
    plus auto-generated NFP Fridays. De-duplicated, sorted by date."""
    events = []
    if os.path.exists(MACRO_FILE):
        try:
            with open(MACRO_FILE, "r", encoding="utf-8") as f:
                events = json.load(f)
        except Exception:
            events = list(DEFAULT_MACRO)
    else:
        events = list(DEFAULT_MACRO)
    events = events + _nfp_fridays()
    seen, uniq = set(), []
    for e in events:
        key = (e.get("date"), e.get("name"))
        if key not in seen and e.get("date"):
            seen.add(key); uniq.append(e)
    uniq.sort(key=lambda e: e["date"])
    return uniq


def upcoming_macro(days=MACRO_LOOKAHEAD):
    today = _today()
    horizon = today + dt.timedelta(days=days)
    out = []
    for e in load_macro():
        try:
            d = dt.date.fromisoformat(e["date"])
        except Exception:
            continue
        if today <= d <= horizon:
            out.append({**e, "days": (d - today).days})
    return out


def macro_text():
    ev = upcoming_macro()
    if not ev:
        return ("🏦 <b>Macro — next 7 days</b>\nNothing major scheduled.\n"
                "<i>Curated list — verify dates against official schedules.</i>")
    lines = ["🏦 <b>Macro events — next 7 days</b>", ""]
    for e in ev:
        when = "TODAY" if e["days"] == 0 else ("tomorrow" if e["days"] == 1
                                               else f"in {e['days']}d")
        flag = "⚠️ " if e["days"] <= 1 else ""
        lines.append(f"{flag}<b>{when}</b> · {e['date']} {e.get('cet','')} CET\n"
                     f"   {e['name']}")
    lines.append("\n<i>Market-moving US releases. Expect volatility around these "
                 "— size smaller or wait. Dates best-effort; verify officially.</i>")
    return "\n".join(lines)


# ------------------------------------------------------------- earnings
def upcoming_earnings(days=EARN_LOOKAHEAD):
    out = []
    for t in s.WATCHLIST:
        if t in ("SPY", "QQQ"):
            continue
        dte = s.days_to_earnings(t)
        if dte is not None and 0 <= dte <= days:
            out.append({"ticker": t, "days": dte, "name": s.name_for(t)})
    out.sort(key=lambda x: x["days"])
    return out


def earnings_text():
    ev = upcoming_earnings()
    if not ev:
        return ("📅 <b>Earnings — next 7 days</b>\nNone of your watchlist reports "
                "in the next 7 days.")
    lines = ["📅 <b>Earnings — next 7 days</b>",
             "<i>gap risk around reports — size small or skip just before</i>", ""]
    for e in ev:
        when = "TODAY" if e["days"] == 0 else ("tomorrow" if e["days"] == 1
                                               else f"in {e['days']}d")
        nm = f" · {e['name']}" if e["name"] else ""
        lines.append(f"• <b>{e['ticker']}</b>{nm} — {when}")
    return "\n".join(lines)


# ---------------------------------------------------------------- send
def daily_digest(send=True):
    """Send earnings + macro digests. Called by the daily workflow."""
    parts = [earnings_text(), macro_text()]
    text = "\n\n".join(parts)
    if send:
        s.send_telegram(earnings_text())
        s.send_telegram(macro_text())
    print(text.replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", "")
              .encode("ascii", "replace").decode("ascii"))
    return text


if __name__ == "__main__":
    if "--sources" in sys.argv:
        print("BLS  (CPI/PPI/Jobs/Retail): https://www.bls.gov/schedule/news_release/")
        print("Fed  (FOMC):  https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
        print("BEA  (GDP/PCE): https://www.bea.gov/news/schedule")
    elif "--print" in sys.argv:
        daily_digest(send=False)
    else:
        daily_digest(send=True)
