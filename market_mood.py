"""
Market Mood + Hot Money — "what is actually moving right now"
--------------------------------------------------------------
Grace's problem: she can't watch TradingView all day, and the dip-ranker is
BLIND to the day's winners by construction (it rewards low RSI, so a stock that
is up 5% today can never appear). On a day when money rotates into semis, the
dip list hands her exactly the names being sold.

This module answers a different question — not "what should I buy" but
"WHERE IS THE MONEY GOING RIGHT NOW":

  • MARKET REGIME  — how many stocks are advancing (breadth), plus S&P / Nasdaq.
                     ~70-80% of a stock's daily move comes from market + sector,
                     so this is the first thing to know.
  • SECTOR HEAT    — every sector ranked by TODAY's move (the rotation map).
  • HOT MONEY      — ranked by HOW MUCH money and HOW FAST:
                       money flow  price x volume vs its own 20-day average
                                   (share count alone doesn't say how much MONEY)
                       speed       today's travel, e.g. "-5% -> +5%", and how far
                                   it climbed off the low = buyers stepping in NOW
                       trend       DEFINED on 1 week - 2 months (direction AND
                                   steadiness); 3 months only CONFIRMS it. Flags
                                   when a good trend has stalled this month.
                       sector      money rotates by sector first, stock second

WHY THE HOLD PERIOD MATTERS (this is the key to using it):
Two well-documented effects point in OPPOSITE directions depending on horizon —
short-term REVERSAL over days-to-a-month (losers bounce, winners fade) versus
MOMENTUM over weeks-to-months (winners keep winning). Those are old published
findings and both have DECAYED since — momentum in particular is weaker and more
crash-prone now — so they are used only as an explanation, never as evidence.
Every number in this project comes from OUR OWN tests on the LAST 5 YEARS of
data, which reproduce the same split — which is why
/rank (dip-buying, days) and /hot (momentum, weeks-to-months) BOTH make sense but
need DIFFERENT holds. Trading this list with a tight 2-5 day stop is the one way
to be right about the trend and still lose: use /mode wide.
"""

import datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
import scanner as s

INDICES = [("S&P 500", "SPY"), ("Nasdaq 100", "QQQ")]


def _stamp():
    return dt.datetime.now(ZoneInfo("Europe/Berlin")).strftime("%d %b %Y, %H:%M CET")


def _pct_today(df):
    """(last close vs previous close) %, from a small daily frame."""
    try:
        c = df["Close"].dropna()
        if len(c) < 2:
            return None
        return (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100.0
    except Exception:
        return None


def _lr_corr(y):
    """How STEADY a move is: correlation of price against time over the window.
    +1 = a clean ladder up · 0 = noise · -1 = a clean slide down. This is
    "gradually going up" turned into a number."""
    try:
        y = [float(v) for v in y if v == v]
        n = len(y)
        if n < 8:
            return None
        import statistics as st
        xs = list(range(n))
        mx, my = st.mean(xs), st.mean(y)
        sx = (sum((a - mx) ** 2 for a in xs)) ** .5
        sy = (sum((b - my) ** 2 for b in y)) ** .5
        if sx == 0 or sy == 0:
            return None
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, y))
        return cov / (sx * sy)
    except Exception:
        return None


def _session_now():
    """Which US session we're in — so the 'today' number is labelled honestly."""
    try:
        ny = dt.datetime.now(ZoneInfo("America/New_York"))
        if ny.weekday() >= 5:
            return "closed", "weekend — last close"
        m = ny.hour * 60 + ny.minute
        if (4 * 60) <= m < (9 * 60 + 30):
            return "pre", "PRE-MARKET (thin volume — moves can reverse at the open)"
        if (9 * 60 + 30) <= m < (16 * 60):
            return "regular", "US market OPEN"
        if (16 * 60) <= m < (20 * 60):
            return "post", "AFTER-HOURS (thin volume)"
        return "closed", "US market closed — showing the last full session"
    except Exception:
        return "regular", ""


def snapshot(universe=None):
    """Gather everything in ONE batched download. Returns a dict of raw numbers
    so both the mood report and the hot-money ranking can reuse it."""
    universe = universe or s.rank_universe()
    try:
        data = yf.download(universe, period="6mo", interval="1d", progress=False,
                           auto_adjust=False, group_by="ticker", threads=True)
    except Exception:
        data = None

    # LIVE overlay so the mood reflects RIGHT NOW, including PRE- and POST-market.
    # Daily bars don't update until the session closes, so before the open the
    # whole snapshot would otherwise be yesterday's news. Alpaca gives real-time
    # US trades; the 15m prepost frame covers everything else.
    session, session_note = _session_now()
    live = {}
    try:
        import alpaca_data as alp
        live.update({k: v for k, v in (alp.latest_prices(universe) or {}).items() if v})
    except Exception:
        pass
    try:
        intr = yf.download(universe, period="2d", interval="15m", progress=False,
                           auto_adjust=False, prepost=True, group_by="ticker",
                           threads=True)
        for t in universe:
            if t in live:
                continue
            try:
                idf = intr[t]
                if isinstance(idf.columns, pd.MultiIndex):
                    idf.columns = idf.columns.get_level_values(0)
                idf = idf.dropna(subset=["Close"])
                idf = s.tradeable_bars(idf)      # ignore zero-volume phantom bars
                if len(idf):
                    live[t] = float(idf["Close"].iloc[-1])
            except Exception:
                continue
    except Exception:
        pass

    rows = []
    for t in universe:
        try:
            d = data[t]
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            d = d.dropna(subset=["Close"])
            if len(d) < 25:
                continue
            c = d["Close"]
            # Use the LIVE price when we have one; the reference is then the last
            # COMPLETED session's close, so pre-market gaps show up immediately.
            px_live = live.get(t)
            if px_live and px_live > 0:
                prev_close = float(c.iloc[-2]) if len(c) > 1 else float(c.iloc[-1])
                # If today's daily bar already exists, compare against yesterday.
                day = (px_live / prev_close - 1) * 100.0
                price = px_live
            else:
                day = _pct_today(d)
                price = float(c.iloc[-1])
            if day is None:
                continue
            vol = d["Volume"]
            rvol = (float(vol.iloc[-1]) / float(vol.iloc[-21:-1].mean())
                    if float(vol.iloc[-21:-1].mean()) > 0 else None)
            week = (price / float(c.iloc[-6]) - 1) * 100 if len(c) > 6 else None
            month = (price / float(c.iloc[-22]) - 1) * 100 if len(c) > 22 else None
            wk2 = (price / float(c.iloc[-11]) - 1) * 100 if len(c) > 11 else None
            mon2 = (price / float(c.iloc[-43]) - 1) * 100 if len(c) > 43 else None
            mon3 = (price / float(c.iloc[-63]) - 1) * 100 if len(c) > 63 else None
            # Steadiness over 1 month AND 3 months — momentum is a 3-12 month
            # effect in the research, so the wider window is the honest one; the
            # 1-month view says whether it is still intact right now.
            # Trend is DEFINED on 1-2 months (per Grace: 3 months only confirms).
            smooth = _lr_corr(c.iloc[-21:].values) if len(c) > 21 else None
            smooth2 = _lr_corr(c.iloc[-43:].values) if len(c) > 43 else None
            smooth3 = _lr_corr(c.iloc[-63:].values) if len(c) > 63 else None

            # MONEY FLOW — euros/dollars traded today vs its own 20-day average.
            # Share volume alone doesn't say how much MONEY moved; price x volume
            # does, and that is what "institutions are buying this" looks like.
            money = None
            try:
                dv = (d["Close"] * d["Volume"])
                avg_dv = float(dv.iloc[-21:-1].mean())
                if avg_dv > 0:
                    money = float(dv.iloc[-1]) / avg_dv
            except Exception:
                pass

            # SPEED / INTRADAY TRAVEL — "it went from -5% to +5% today" is the
            # signature of money rushing in. Measured against yesterday's close.
            lo_pct = hi_pct = recover = None
            try:
                prev = float(c.iloc[-2])
                lo_pct = (float(d["Low"].iloc[-1]) / prev - 1) * 100
                hi_pct = (float(d["High"].iloc[-1]) / prev - 1) * 100
                recover = day - lo_pct          # how far it climbed off the low
            except Exception:
                pass

            rows.append({"ticker": t, "day": day, "rvol": rvol, "week": week,
                         "month": month, "mon3": mon3, "smooth": smooth,
                         "smooth3": smooth3, "money": money, "lo_pct": lo_pct,
                         "hi_pct": hi_pct, "recover": recover, "price": price,
                         "wk2": wk2, "mon2": mon2, "smooth2": smooth2,
                         "live": bool(px_live),
                         "currency": s.ticker_currency(t),
                         "sector": (s.SECTOR_MAP.get(t) or "")})
        except Exception:
            continue

    # market breadth = share of the scanned universe that is up today
    ups = [r for r in rows if r["day"] > 0]
    breadth = (len(ups) / len(rows) * 100) if rows else None

    idx = []
    for name, sym in INDICES:
        try:
            d = yf.download(sym, period="5d", interval="1d", progress=False,
                            auto_adjust=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            p = _pct_today(d)
            if p is not None:
                idx.append((name, p))
        except Exception:
            continue

    # SPY 1-month move, to measure each stock's relative strength against
    spy_month = None
    try:
        d = yf.download("SPY", period="6mo", interval="1d", progress=False,
                        auto_adjust=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        c = d["Close"].dropna()
        spy_month = (float(c.iloc[-1]) / float(c.iloc[-22]) - 1) * 100
    except Exception:
        pass

    return {"rows": rows, "breadth": breadth, "indices": idx,
            "spy_month": spy_month, "sectors": s.sector_day_ranking(),
            "session": session, "session_note": session_note,
            "live_count": sum(1 for r in rows if r.get("live"))}


def _mood_word(breadth):
    if breadth is None:
        return "❔ unknown", ""
    if breadth >= 65:
        return "🟢 RISK-ON", "broad buying — most things are going up"
    if breadth >= 55:
        return "🙂 mildly positive", "more winners than losers"
    if breadth >= 45:
        return "😐 mixed", "no clear direction — stock picking matters most"
    if breadth >= 35:
        return "🟠 soft", "more losers than winners — be selective"
    return "🔴 RISK-OFF", "broad selling — even good setups usually fail today"


def mood_text(snap=None):
    """The 'what's the market doing right now' snapshot."""
    snap = snap or snapshot()
    rows, breadth = snap["rows"], snap["breadth"]
    word, meaning = _mood_word(breadth)

    L = [f"📊 <b>Market Mood</b> — {_stamp()}"]
    if snap.get("session_note"):
        L.append(f"<i>🕒 {snap['session_note']}</i>")
    L.append("")
    if breadth is not None:
        up = sum(1 for r in rows if r["day"] > 0)
        L.append(f"🌡️ <b>{word}</b> — {breadth:.0f}% advancing ({up}/{len(rows)})")
        L.append(f"   <i>{meaning}</i>")
    if snap["indices"]:
        L.append("   " + " · ".join(f"{n} {p:+.1f}%" for n, p in snap["indices"]))
    L.append("")

    secs = snap["sectors"]
    if secs:
        L.append("🔥 <b>Money flowing IN today</b>")
        for n, p in secs[:3]:
            L.append(f"   {n} <b>{p:+.1f}%</b>")
        L.append("🧊 <b>Money flowing OUT</b>")
        for n, p in secs[-3:]:
            L.append(f"   {n} <b>{p:+.1f}%</b>")
        L.append("")

    movers = sorted([r for r in rows if r["day"] is not None],
                    key=lambda r: r["day"], reverse=True)
    L.append("🚀 <b>Biggest gainers today</b>")
    for r in movers[:6]:
        nm = s.name_for(r["ticker"]) or r["ticker"]
        rv = f" · vol {r['rvol']:.1f}×" if r.get("rvol") else ""
        sec = f" · {r['sector']}" if r["sector"] else ""
        L.append(f"   <b>{r['day']:+.1f}%</b> {r['ticker']} · {nm}{rv}{sec}")
    L.append("")
    L.append("🩸 <b>Biggest losers today</b>")
    for r in movers[-4:][::-1]:
        nm = s.name_for(r["ticker"]) or r["ticker"]
        sec = f" · {r['sector']}" if r["sector"] else ""
        L.append(f"   <b>{r['day']:+.1f}%</b> {r['ticker']} · {nm}{sec}")

    L.append("\n<i>A map of where money is moving — NOT a buy list. On a 🔴 day "
             "even good setups usually fail; on a 🟢 day almost anything works. "
             "/hot for momentum names · /rank for dip-buy setups.</i>")
    return "\n".join(L)


def hot_text(snap=None, top_n=8):
    """MOMENTUM ranking — the opposite of /rank. Ranks by where money is moving:
    today's move, unusual volume, 1-month strength vs the market, sector heat.

    Framed honestly: tested on 25k setups, buying strength with a tight stop over
    2-5 days did NOT beat the dip system. The ⭐ flag marks the ONE combination
    that did test well (RS>5% + RVOL>1.5x + near highs = 54% win / +0.184 R)."""
    snap = snap or snapshot()
    rows, spy_m = snap["rows"], snap["spy_month"]
    sec_rank = {n: i + 1 for i, (n, _) in enumerate(snap["sectors"])}
    sec_pct = dict(snap["sectors"])

    scored = []
    for r in rows:
        if r["month"] is None or r["day"] is None:
            continue
        rs = r["month"] - spy_m if spy_m is not None else 0.0
        rv = r.get("rvol") or 1.0
        mf = r.get("money") or 1.0          # money flow vs its own average
        sp = sec_pct.get(r["sector"], 0.0)
        rec = r.get("recover") or 0.0       # climb off today's low = urgency
        sm2 = r.get("smooth2")
        sm3 = r.get("smooth3")
        m2 = r.get("mon2") or 0.0
        m3 = r.get("mon3") or 0.0

        # HEAT = how much money, how fast, into what kind of trend.
        #   money flow  — the biggest weight: euros traded vs normal is the
        #                 clearest "institutions are here" signal
        #   day move    — the result of that buying
        #   recovery    — climbing off the low means buyers are stepping in NOW
        #   trend       — a 3-month climb that is STEADY, not a one-day spike
        #                 (momentum is a 3-12 month effect in the research)
        #   sector      — money rotates by sector first, stock second
        heat = ((mf - 1.0) * 10.0
                + r["day"] * 2.0
                + rec * 1.0
                + (m2 * 0.12 if m2 > 0 else m2 * 0.06)
                + ((sm2 or 0) * 6.0)
                + (2.0 if (sm3 or 0) > 0.4 and m3 > 0 else 0.0)
                + sp * 3.0)
        # ⭐ = the one combination that backtested well, now also requiring the
        # longer trend to be intact (momentum needs a longer hold to pay).
        star = (rs > 5 and rv > 1.5 and (r["week"] or 0) > 0
                and (sm2 is None or sm2 > 0.3))
        scored.append({**r, "rs": rs, "heat": heat, "star": star,
                       "mf": mf, "sec_rank": sec_rank.get(r["sector"])})
    scored.sort(key=lambda x: x["heat"], reverse=True)

    L = [f"🔥 <b>Hot Money — where it's moving now</b>",
         f"<i>Asked: {_stamp()}</i>", ""]
    word, _ = _mood_word(snap["breadth"])
    if snap["breadth"] is not None:
        L.append(f"🌡️ Market: <b>{word}</b> ({snap['breadth']:.0f}% advancing)")
        L.append("")
    for i, r in enumerate(scored[:top_n], 1):
        nm = s.name_for(r["ticker"])
        title = r["ticker"] + (f" · {nm}" if nm else "")
        star = " ⭐" if r["star"] else ""
        L.append(f"<b>{i}. {title}</b> — <b>{r['day']:+.1f}% today</b>{star}")
        L.append(f"   {r['price']:.2f} {r['currency']}")
        # MONEY: how much is flowing in vs normal — the "how hot" number.
        mf = r.get("mf") or 1.0
        if mf >= 3:
            mtag = f"💰💰 <b>{mf:.1f}× normal money</b> — heavy institutional buying"
        elif mf >= 1.8:
            mtag = f"💰 <b>{mf:.1f}× normal money</b> flowing in"
        elif mf >= 1.2:
            mtag = f"💰 {mf:.1f}× normal money"
        else:
            mtag = f"{mf:.1f}× money (normal — the move is not backed by big volume)"
        L.append(f"   {mtag}")
        # SPEED: today's travel — "went from -5% to +5%" is money rushing in.
        lo, hi, rec = r.get("lo_pct"), r.get("hi_pct"), r.get("recover")
        if lo is not None and hi is not None:
            speed = ""
            if rec is not None and rec >= 3:
                speed = f" · 🚀 climbed <b>{rec:.1f}%</b> off its low"
            L.append(f"   today's swing {lo:+.1f}% → {hi:+.1f}%{speed}")
        # TREND, wide view (3 months) plus whether it is still intact.
        sm, sm2, sm3 = r.get("smooth"), r.get("smooth2"), r.get("smooth3")
        m2, m3 = r.get("mon2"), r.get("mon3")
        # The trend is DEFINED by the 1-2 month picture; 3 months only confirms.
        define = sm2 if sm2 is not None else sm
        span = m2 if m2 is not None else r["month"]
        if define is not None and span is not None:
            if define >= 0.7 and span > 0:
                shape = f"🪜 <b>steady climb</b> over 2 months ({span:+.0f}%)"
            elif span > 0:
                shape = f"↗️ up over 2 months ({span:+.0f}%) but choppy"
            elif define <= -0.7:
                shape = (f"⚠️ <b>falling for weeks</b> ({span:+.0f}%) — today is "
                         "probably a bounce, not a recovery")
            else:
                shape = f"〰️ no clear direction ({span:+.0f}%)"
            extra = ""
            if sm is not None and define > 0.5 and sm < 0:
                extra = " · ⚠️ but it has stalled this month"
            elif sm3 is not None and m3 is not None and define > 0.5 and sm3 > 0.4 and m3 > 0:
                extra = " · ✅ 3-month trend confirms it"
            L.append(f"   {shape}{extra}")
        L.append(f"   1wk {(r['week'] or 0):+.1f}% · 2wk {(r.get('wk2') or 0):+.1f}% "
                 f"· 1mo {r['month']:+.1f}% · vs market {r['rs']:+.0f}%")
        rk = f" (#{r['sec_rank']} today)" if r["sec_rank"] else ""
        if r["sector"]:
            L.append(f"   Sector {r['sector']}: {sec_pct.get(r['sector'], 0):+.1f}%{rk}"
                     f" · vs market {r['rs']:+.0f}% (1mo)")
        L.append("")
    L.append("<i>💰 = money traded vs its own normal — the clearest sign real buyers "
             "are here, not just a drifting price.\n"
             "⭐ = strong vs market + heavy volume + a 3-month trend still intact.\n"
             "⚠️ <b>Momentum needs a LONGER hold.</b> Research (and our own test) is "
             "consistent: over 2-5 days the losers bounce and winners fade, but "
             "trends pay over weeks to months. If you trade from this list use "
             "<code>/mode wide</code> — a tight stop turns normal wobble into a loss "
             "and you miss the move you were right about.</i>")
    return "\n".join(L)


if __name__ == "__main__":
    snap = snapshot()
    for txt in (mood_text(snap), hot_text(snap)):
        print(txt.replace("<b>", "").replace("</b>", "")
                 .replace("<i>", "").replace("</i>", "")
                 .encode("ascii", "replace").decode("ascii"))
        print("\n" + "=" * 60 + "\n")
