"""
Earnings guard — risk BEFORE the print, not a prediction of it
---------------------------------------------------------------
Tested against three real cases from the same week (INTU crashed, CRM +12%,
CRWD spiked): NO pre-earnings technical separated them. INTU walked in at
RSI 55 and +1.4% over its 20-EMA — the calmest of the three — beat EPS by 12%
and still fell. So this module does not tell you which way a stock will go. It
tells you HOW MUCH is at stake and WHETHER YOUR STOP CAN PROTECT YOU.

Four things it reports, in order of how much they matter:

  1. GAP RISK — the one that actually changes a decision. A 1.5xATR stop cannot
     execute through an earnings gap: the stock reopens below it. True at RSI 42
     or 78, which is exactly why a technical filter cannot catch it.
  2. TYPICAL REACTION — what this stock has ACTUALLY done on its last several
     earnings days. A real, measured number, available for every ticker.
  3. IMPLIED MOVE — the option straddle's expected move. The market's own
     estimate, with money behind it. Live only, US names with liquid options.
  4. EXTENSION — RSI + distance from the 20-EMA, plus the 1-month run-up.
     CONTEXT ONLY, never a filter. Shown because it is the honest input Gemini's
     "priced for perfection" idea was reaching for, but the run-up did NOT
     separate the crash from the spikes (CRM ran up MORE than INTU and spiked),
     so it is displayed with that caveat rather than used as a trigger.

`as_of` lets every function be evaluated at a past date, so any rule here can be
checked against what the bot WOULD have said before a print.
"""

import datetime as dt
from zoneinfo import ZoneInfo
import yfinance as yf
import scanner as s

ET = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")

_HIST = {}
_EARN = {}


def _hist(ticker: str):
    if ticker not in _HIST:
        try:
            d = yf.Ticker(ticker).history(period="5y", auto_adjust=False)
            _HIST[ticker] = d.dropna(subset=["Close"])
        except Exception:
            _HIST[ticker] = None
    return _HIST[ticker]


def _earn_dates(ticker: str):
    """All known earnings timestamps (past + future), newest first."""
    if ticker not in _EARN:
        try:
            ed = yf.Ticker(ticker).get_earnings_dates(limit=24)
            _EARN[ticker] = list(ed.index) if ed is not None and len(ed) else []
        except Exception:
            _EARN[ticker] = []
    return _EARN[ticker]


def _reaction_day(ts, idx):
    """The session that REACTS to a report filed at `ts`.

    After the close (>= noon ET) -> the next session. Before the open -> the
    same session. Getting this wrong measures the wrong day entirely.
    """
    et = ts.astimezone(ET)
    if et.hour < 12:
        days = [d for d in idx if d.date() >= et.date()]
    else:
        days = [d for d in idx if d.date() > et.date()]
    return days[0] if days else None


def past_reactions(ticker: str, n: int = 6, as_of=None):
    """How this stock ACTUALLY moved on its last `n` earnings reaction days.

    Returns {"moves": [+4.2, -8.1, ...], "avg_abs": 6.4, "worst": -8.1} or None.
    This is the honest stand-in for an implied move: it needs no options chain,
    works for European tickers, and can be computed for any past date.
    """
    d = _hist(ticker)
    if d is None or d.empty:
        return None
    if as_of is not None:
        d = d[d.index.date <= as_of]
    if len(d) < 30:
        return None
    c = d["Close"]
    moves = []
    for ts in _earn_dates(ticker):
        day = _reaction_day(ts, d.index)
        if day is None:
            continue
        i = d.index.get_loc(day)
        if i < 1:
            continue
        mv = (float(c.iloc[i]) / float(c.iloc[i - 1]) - 1) * 100
        moves.append(round(mv, 1))
        if len(moves) >= n:
            break
    if not moves:
        return None
    return {"moves": moves,
            "avg_abs": sum(abs(m) for m in moves) / len(moves),
            "worst": min(moves), "best": max(moves)}


def implied_move(ticker: str, earn_ts=None):
    """Expected move from the at-the-money straddle, via events.implied_move.

    Deliberately NOT reimplemented here: events.py already prices the straddle
    off the bid/ask MID, which is more accurate than last-traded (option last
    prices go stale between trades). One implementation, so the two can never
    disagree. Imported lazily to avoid an import cycle.

    Live only — historical option prices are not available, so a past `as_of`
    gets None rather than a guess.
    """
    try:
        import events as _ev
        return _ev.implied_move(ticker)
    except Exception:
        return None


def extension(ticker: str, as_of=None):
    """RSI, distance from the 20-EMA and the 1-month run-up. CONTEXT ONLY."""
    d = _hist(ticker)
    if d is None or d.empty:
        return None
    if as_of is not None:
        d = d[d.index.date <= as_of]
    if len(d) < 25:
        return None
    c = d["Close"]
    ema20 = c.ewm(span=20).mean()
    r = s.rsi(c, 14)
    return {"price": float(c.iloc[-1]),
            "rsi": float(r.iloc[-1]),
            "vs_ema": (float(c.iloc[-1]) / float(ema20.iloc[-1]) - 1) * 100,
            "runup": (float(c.iloc[-1]) / float(c.iloc[-22]) - 1) * 100,
            "date": c.index[-1].date()}


def next_earnings(ticker: str, as_of=None):
    """The next earnings timestamp strictly after `as_of` (default: now)."""
    if as_of is None:
        ref = dt.datetime.now(ET)
    else:
        ref = dt.datetime.combine(as_of, dt.time(9, 0), tzinfo=ET)
    fut = [t for t in _earn_dates(ticker) if t.astimezone(ET) >= ref]
    return min(fut) if fut else None


def _stop_pct(ticker, price, as_of=None):
    """How far below price the ATR stop sits, in %, using the ACTIVE mode."""
    try:
        d = _hist(ticker)
        if d is None:
            return None
        if as_of is not None:
            d = d[d.index.date <= as_of]
        a = float(s.atr(d, 14).iloc[-1])
        return a * s.active_stop_mult() / price * 100
    except Exception:
        return None


def guard(ticker: str, as_of=None):
    """Everything the guard knows, as a dict. `as_of` = a date object."""
    ts = next_earnings(ticker, as_of=as_of)
    ext = extension(ticker, as_of=as_of)
    react = past_reactions(ticker, as_of=as_of)
    imp = implied_move(ticker, ts) if as_of is None else None
    ref = as_of or dt.datetime.now(ET).date()
    days = after_close = None
    if ts is not None:
        et = ts.astimezone(ET)
        days = (et.date() - ref).days
        after_close = et.hour >= 12
    stop = _stop_pct(ticker, ext["price"], as_of) if ext else None
    return {"ticker": ticker, "earn_ts": ts, "days": days,
            "after_close": after_close, "ext": ext, "react": react,
            "implied": imp, "stop_pct": stop, "as_of": ref}


def guard_text(ticker: str, as_of=None) -> str:
    """The Telegram message. Plain language, no jargon left unexplained."""
    g = guard(ticker, as_of=as_of)
    name = s.name_for(ticker) or ticker
    ts, days, ext, react = g["earn_ts"], g["days"], g["ext"], g["react"]
    if ts is None:
        # Two very different situations that both look like "no date":
        #   * an ETF (SPY, QQQ) never reports at all
        #   * a company that JUST reported — the next date is not published yet
        # Saying "no earnings" for the second one would be misleading.
        past = _earn_dates(ticker)
        if not past:
            return (f"\U0001f4c5 <b>{name}</b> ({ticker}) does not report "
                    "earnings — it is a fund/ETF, not a company.")
        last = max(past).astimezone(BERLIN)
        return (f"\U0001f4c5 <b>{name}</b> ({ticker}) — next earnings date not "
                f"announced yet.\nLast report: {last.strftime('%d %b %Y')}. "
                "Companies usually confirm the next date a few weeks ahead; the "
                "guard will pick it up automatically.")
    when = ts.astimezone(BERLIN)
    # "after the US close" only means something for a US-listed stock. European
    # tickers carry an exchange suffix (SAP.DE, NESN.SW) and trade on their own
    # session, so describing them in US terms is simply wrong.
    is_us = "." not in ticker
    slot = ""
    if is_us:
        slot = (" (after the US close)" if g["after_close"]
                else " (before the US open)")
    soon = days is not None and days <= 7
    L = [("⚠️" if soon else "\U0001f4c5") + f" <b>{name}</b> ({ticker}) reports "
         f"<b>{when.strftime('%a %d %b, %H:%M')} German time</b>{slot}"]
    if days == 0:
        L.append("\U0001f534 <b>That is TODAY.</b>")
    elif days == 1:
        L.append("\U0001f7e0 <b>That is TOMORROW.</b>")
    elif soon:
        L.append(f"\U0001f7e1 In <b>{days} days</b>.")
    else:
        L.append(f"In <b>{days} days</b> — far off, nothing to act on yet.")
    L.append("")

    # Beyond a week the gap-risk alarm is noise; show the history as reference
    # and stop there.
    if not soon:
        if react:
            hist = " · ".join(f"{m:+.1f}%" for m in react["moves"])
            L.append(f"\U0001f4ca <b>For reference, its last "
                     f"{len(react['moves'])} earnings days:</b> {hist}")
            L.append(f"Typical swing <b>±{react['avg_abs']:.1f}%</b> · "
                     f"worst <b>{react['worst']:+.1f}%</b>")
        else:
            L.append("<i>No past earnings-day moves available for this stock.</i>")
        return "\n".join(L)

    # 1. GAP RISK — the part that changes a decision.
    if g["stop_pct"] and react:
        stop, typ, worst = g["stop_pct"], react["avg_abs"], abs(react["worst"])
        if typ >= stop:
            L.append("\U0001f6d1 <b>Your stop cannot protect you here.</b>")
            L.append(f"Your stop sits about <b>{stop:.1f}%</b> below price, but "
                     f"this stock <b>typically</b> moves <b>±{typ:.1f}%</b> on "
                     "earnings day. It reopens past your stop, so you exit at "
                     "whatever the gap gives you — not at your price.")
        elif worst >= stop:
            L.append("\U0001f7e0 <b>Your stop covers a normal move, not a bad one.</b>")
            L.append(f"Your stop sits ~<b>{stop:.1f}%</b> below price and the "
                     f"typical earnings move is <b>±{typ:.1f}%</b> — inside it. "
                     f"But this stock has gapped <b>{react['worst']:+.1f}%</b> "
                     "before, which your stop would not have caught.")
        else:
            L.append("\U0001f7e2 <b>Lightest case — but still a gap.</b>")
            L.append(f"Your stop sits ~<b>{stop:.1f}%</b> below price and this "
                     f"stock's earnings moves (typical <b>±{typ:.1f}%</b>, worst "
                     f"<b>{react['worst']:+.1f}%</b>) have stayed inside it. A "
                     "surprise still opens past it — history is not a cap.")
        L.append("")
    elif g["stop_pct"]:
        # No usable earnings history — common for semi-annual European reporters
        # (Nestlé, Adyen), where the data provider lists only a couple of dates.
        # Say so plainly instead of quietly dropping the section.
        L.append(f"\U0001f6d1 Your stop sits ~<b>{g['stop_pct']:.1f}%</b> below "
                 "price. An earnings gap opens past it — it does not execute.")
        L.append("<i>No past earnings-day moves available for this stock, so "
                 "there is no typical swing to compare your stop against. Treat "
                 "the risk as unknown rather than small.</i>")
        L.append("")

    # 2. WHAT IT ACTUALLY DOES on earnings day.
    if react:
        hist = " · ".join(f"{m:+.1f}%" for m in react["moves"])
        L.append(f"\U0001f4ca <b>Last {len(react['moves'])} earnings days:</b> {hist}")
        L.append(f"Typical swing <b>±{react['avg_abs']:.1f}%</b> · "
                 f"worst <b>{react['worst']:+.1f}%</b> · "
                 f"best <b>{react['best']:+.1f}%</b>")
        L.append("")

    # 3. IMPLIED MOVE — live only, never guessed.
    if g["implied"]:
        L.append(f"\U0001f3b2 <b>Options expect ±{g['implied']['pct']:.1f}%</b> "
                 f"(traders' own money, expiry {g['implied'].get('exp', '')}). "
                 "That is SIZE, not direction.")
        L.append("")

    # 4. EXTENSION — context, with the caveat attached.
    if ext:
        L.append(f"\U0001f4c8 Context: RSI <b>{ext['rsi']:.0f}</b> · "
                 f"<b>{ext['vs_ema']:+.1f}%</b> vs its 20-day average · "
                 f"1-month run-up <b>{ext['runup']:+.1f}%</b>")
        L.append("<i>Context only. Tested on INTU/CRM/CRWD in the same week: "
                 "none of these separated the crash from the spikes. Do not read "
                 "a direction into them.</i>")
        L.append("")

    L.append("<b>The honest part:</b> nobody can tell you which way this goes. "
             "Your choice is whether to hold through a coin flip your stop "
             "cannot cover — or to trade it AFTER the print, when the "
             "uncertainty is gone and a real setup can form.")
    return "\n".join(L)
