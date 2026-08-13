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
import requests
import pandas as pd
import scanner as s
import ff_calendar as ff

MACRO_FILE      = "macro_calendar.json"
EARN_LOOKAHEAD  = 7    # list earnings within this many days
MACRO_LOOKAHEAD = 7    # list macro events within this many days

# FRED (free) gives the OFFICIAL scheduled release dates. Set a free key as the
# FRED_API_KEY secret to switch macro dates from the curated fallback to live,
# accurate ones. Get a key in 1 min: https://fredaccount.stlouisfed.org/apikeys
# release_id → (display name, CET time). US 8:30am ET releases = 14:30 CET
# year-round (both sides observe DST). These are the standard FRED release IDs.
FRED_RELEASES = [
    (10, "🇺🇸 US CPI (Inflation)",                      "14:30"),
    (50, "🇺🇸 US Jobs report (Employment Situation)",   "14:30"),
    (53, "🇺🇸 US GDP",                                  "14:30"),
    (21, "🇺🇸 US PCE (Personal Income & Outlays)",      "14:30"),
    (46, "🇺🇸 US PPI (Producer Prices)",                "14:30"),
]

# Tracks whether the last load used live FRED dates or the curated fallback.
_SOURCE = {"kind": "curated"}

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


# Impact strength + plain-language meaning per macro type. 🔴 high (moves the
# whole market) · 🟠 medium · 🟡 lower. Sector tilt = the usual reaction (not a
# guarantee — the surprise vs expectations is what actually moves things).
_MACRO_META = [
    (("cpi", "inflation", "consumer price"), "High", "🔴",
     "Inflation reading. Hotter than expected → fear rates stay high → tech, "
     "growth &amp; real estate usually dip, energy/financials hold better. "
     "Cooler → growth &amp; tech tend to rally."),
    (("fomc", "rate decision", "fed", "interest rate"), "High", "🔴",
     "The Fed sets interest rates. Hawkish (higher-for-longer) hurts rate-"
     "sensitive names: tech, real estate, utilities. Dovish (cut/soft tone) lifts them."),
    (("nfp", "jobs", "payroll", "nonfarm", "non farm"), "Medium", "🟠",
     "Jobs report. Very strong jobs can mean rates stay high (stocks wobble); "
     "weak jobs can spark rate-cut hopes — or growth worries."),
    (("pce", "personal consumption"), "Medium", "🟠",
     "The Fed's preferred inflation gauge — same playbook as CPI, usually quieter."),
    (("gdp",), "Medium", "🟡",
     "Growth scorecard. Big surprises move cyclicals — industrials, energy, financials."),
    (("ppi", "producer price", "retail sales"), "Low", "🟡",
     "Secondary inflation/spending data — usually a smaller, shorter market reaction."),
]


def macro_meta(name: str):
    """(impact, emoji, meaning) for a macro event name."""
    low = (name or "").lower()
    for keys, impact, emoji, meaning in _MACRO_META:
        if any(k in low for k in keys):
            return impact, emoji, meaning
    return "Medium", "🟠", "Market-moving US release — expect some volatility."


def _macro_keys(name: str):
    """Keyword list for matching this event against Forex Factory's titles."""
    low = (name or "").lower()
    for keys, _, _, _ in _MACRO_META:
        if any(k in low for k in keys):
            return keys
    return (low,)


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


def _fred_next(release_id, key, count=3):
    """Next scheduled release dates (YYYY-MM-DD) for a FRED release, today onward."""
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/release/dates",
            params={"release_id": release_id, "api_key": key, "file_type": "json",
                    "sort_order": "desc", "limit": "24",
                    "include_release_dates_with_no_data": "true"},
            timeout=15)
        if r.status_code != 200:
            return []
        today = _today().isoformat()
        ds = sorted(d["date"] for d in r.json().get("release_dates", [])
                    if d.get("date") and d["date"] >= today)
        return ds[:count]
    except Exception:
        return []


def _fred_macro(key):
    """Build the macro list from FRED official release dates. None if unavailable."""
    out = []
    for rid, name, cet in FRED_RELEASES:
        for d in _fred_next(rid, key, count=3):
            out.append({"date": d, "cet": cet, "name": name})
    return out or None


def _fomc_curated():
    """FOMC decision dates (not a FRED release — published far ahead, stable)."""
    return [dict(e) for e in DEFAULT_MACRO if "FOMC" in e["name"]]


def load_macro():
    """Macro events, de-duplicated & sorted. Uses live FRED dates when a
    FRED_API_KEY is set (accurate, official) + curated FOMC; otherwise the
    curated fallback (macro_calendar.json or DEFAULT_MACRO + auto NFP Fridays)."""
    key = os.environ.get("FRED_API_KEY")
    live = _fred_macro(key) if key else None
    if live:
        _SOURCE["kind"] = "live"
        events = live + _fomc_curated()
    else:
        _SOURCE["kind"] = "curated"
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
        key2 = (e.get("date"), e.get("name"))
        if key2 not in seen and e.get("date"):
            seen.add(key2); uniq.append(e)
    uniq.sort(key=lambda e: e["date"])
    return uniq


def high_impact_today():
    """Name of a 🔴 high-impact macro event scheduled TODAY, else None."""
    today = _today()
    for e in load_macro():
        try:
            d = dt.date.fromisoformat(e["date"])
        except Exception:
            continue
        if d == today and macro_meta(e["name"])[0] == "High":
            return e["name"]
    return None


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
        return ("🏦 <b>Macro — next 7 days</b>\n\nNothing major scheduled.\n\n"
                "<i>Curated list — verify dates against official schedules.</i>")
    lines = ["🏦 <b>Macro events — next 7 days</b>",
             "🔴 high impact · 🟠 medium · 🟡 lower", ""]
    ff_used = False
    for e in ev:
        when = "TODAY" if e["days"] == 0 else ("tomorrow" if e["days"] == 1
                                               else f"in {e['days']} days")
        impact, emoji, meaning = macro_meta(e["name"])
        warn = " ⚠️" if e["days"] <= 1 and impact == "High" else ""
        # forecast / previous from the free Forex Factory feed (no key)
        exp = ""
        try:
            info = ff.us_expected(_macro_keys(e["name"]), e["date"])
            if info and (info.get("forecast") or info.get("previous")):
                fc = info.get("forecast") or "?"
                pv = info.get("previous") or "?"
                exp = f"   📈 expected <b>{fc}</b> · prev {pv}\n"
                ff_used = True
        except Exception:
            pass
        lines.append(
            f"{emoji} <b>{e['name']}</b>{warn}\n"
            f"   🗓️ {when} · {e['date']} · {e.get('cet','')} CET\n"
            f"{exp}"
            f"   💬 {meaning}\n"
        )
    if _SOURCE["kind"] == "live":
        src = "📡 Dates: live from FRED (official) + curated FOMC."
    else:
        src = ("📝 Dates: curated best-effort — add a free FRED_API_KEY secret for "
               "live official dates. Verify around 🔴 events.")
    if ff_used:
        src += "  📈 Expected/previous: Forex Factory (free)."
    lines.append(f"<i>{src}\nThe reaction depends on the number vs what was "
                 "expected. Around 🔴 events: avoid brand-new entries, wait until "
                 "it settles.</i>")
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


def _earn_when(ticker, dte):
    """'TODAY · Thu 13 Aug, ~14:00 CET' — day label + Berlin date/time. The '~'
    flags that the time is approximate for EU/UK names (see next_earnings_ts)."""
    day = "TODAY" if dte == 0 else ("tomorrow" if dte == 1 else f"in {dte} days")
    ts = s.next_earnings_ts(ticker)
    if ts is None:
        return day
    datestr = ts.strftime("%a %d %b")
    hm = ts.strftime("%H:%M")
    if hm == "00:00":                       # date-only placeholder → no time
        return f"{day} · {datestr}"
    return f"{day} · {datestr}, ~{hm} CET"


def earnings_impact(dte):
    """(emoji, label) for how close/risky an earnings date is."""
    if dte is None:
        return "", ""
    if dte <= 2:
        return "🔴", "very close — don't open new positions"
    if dte <= 4:
        return "🟠", "soon — be cautious"
    if dte <= 7:
        return "🟡", "this week"
    return "", ""


def _current_price(tk):
    try:
        p = tk.fast_info.get("lastPrice") or tk.fast_info.get("last_price")
        if p:
            return float(p)
    except Exception:
        pass
    try:
        return float(tk.history(period="1d")["Close"].iloc[-1])
    except Exception:
        return None


def implied_move(ticker: str):
    """The swing the OPTIONS market expects around the next earnings, as a %% of
    price (the at-the-money straddle for the first expiry after earnings). This is
    a RISK gauge — how big a gap to expect — NOT a direction. None if unavailable."""
    try:
        tk = s.yf.Ticker(ticker)
        exps = list(tk.options or [])
        if not exps:
            return None
        price = _current_price(tk)
        if not price or price <= 0:
            return None
        dte = s.days_to_earnings(ticker)
        target = ((_today() + dt.timedelta(days=dte)).isoformat()
                  if dte is not None else _today().isoformat())
        exp = next((e for e in exps if e >= target), exps[0])
        ch = tk.option_chain(exp)

        def atm(df):
            if df is None or df.empty:
                return None
            row = df.iloc[(df["strike"] - price).abs().argsort().iloc[:1]].iloc[0]
            bid, ask = row.get("bid") or 0, row.get("ask") or 0
            mid = (bid + ask) / 2 if bid and ask else row.get("lastPrice")
            return float(mid) if mid and mid == mid else None

        c, p = atm(ch.calls), atm(ch.puts)
        if not c or not p:
            return None
        return {"pct": (c + p) / price * 100.0, "exp": exp}
    except Exception:
        return None


def next_eps_estimate(ticker: str):
    """Consensus EPS estimate for the next report, or None."""
    try:
        cal = s.yf.Ticker(ticker).get_earnings_dates(limit=8)
        if cal is None or cal.empty:
            return None
        now = pd.Timestamp.now(tz=cal.index.tz)
        fut = cal[cal.index > now]
        if fut.empty:
            return None
        est = fut.iloc[0].get("EPS Estimate")
        return float(est) if est is not None and est == est else None
    except Exception:
        return None


def earnings_warning(ticker: str) -> str:
    """One-line ⚠️ warning for /score if a stock reports soon, with the options'
    expected swing (implied move) + consensus EPS. Empty if no report within window."""
    dte = s.days_to_earnings(ticker)
    if dte is None or dte > EARN_LOOKAHEAD:
        return ""
    emoji, label = earnings_impact(dte)
    when = _earn_when(ticker, dte)
    line = f"\n{emoji} <b>Earnings {when}</b> — {label} (gap risk)"
    im = implied_move(ticker)
    if im:
        line += (f"\n   📊 Options expect a <b>±{im['pct']:.0f}%</b> swing in the SHARE PRICE "
                 f"on the report — a tighter stop will likely be gapped through.")
    est = next_eps_estimate(ticker)
    if est is not None:
        line += (f"\n   🔢 Consensus EPS estimate: {est:.2f} "
                 "<i>(what analysts expect; the beat/miss reaction is unpredictable)</i>")
    return line


def earnings_text():
    ev = upcoming_earnings()
    if not ev:
        return ("📅 <b>Earnings — next 7 days</b>\n\nNone of your watchlist reports "
                "in the next 7 days.")
    lines = ["📅 <b>Earnings — next 7 days</b>",
             "🔴 ≤2 days · 🟠 3-4 · 🟡 5-7  (gap risk — avoid new entries just before)",
             ""]
    for e in ev:
        when = _earn_when(e["ticker"], e["days"])
        emoji, _ = earnings_impact(e["days"])
        nm = f" · {e['name']}" if e["name"] else ""
        im = implied_move(e["ticker"])
        mv = f" · options expect ±{im['pct']:.0f}%" if im else ""
        lines.append(f"{emoji} <b>{e['ticker']}</b>{nm} — {when}{mv}")
    lines.append("\n<i>±% = the swing the options market prices in for the report "
                 "(a risk gauge, not a direction). Times are in CET — exact for US "
                 "names, approximate for EU/UK.</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------- send
def daily_digest(send=True):
    """Send earnings + macro + market-news digests. Called by the daily workflow."""
    try:
        import news as nw
        market = nw.market_news_text()
    except Exception:
        market = ""
    parts = [earnings_text(), macro_text()] + ([market] if market else [])
    text = "\n\n".join(parts)
    if send:
        for p in parts:
            s.send_telegram(p)
    print(text.replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", "")
              .encode("ascii", "replace").decode("ascii"))
    return text


if __name__ == "__main__":
    if "--sources" in sys.argv:
        print("Live dates: set FRED_API_KEY (free) -> https://fredaccount.stlouisfed.org/apikeys")
        print("Official schedules to verify against:")
        print("  BLS  (CPI/PPI/Jobs/Retail): https://www.bls.gov/schedule/news_release/")
        print("  Fed  (FOMC):  https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
        print("  BEA  (GDP/PCE): https://www.bea.gov/news/schedule")
    elif "--print" in sys.argv:
        daily_digest(send=False)
    else:
        daily_digest(send=True)
