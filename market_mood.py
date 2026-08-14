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
            # Reference = the last daily close BEFORE today. Before the US open
            # there is no bar for today yet, so the last bar IS yesterday's close
            # and must be used as the reference, not skipped. Getting this wrong
            # is why /hot looked frozen at yesterday's numbers.
            today_local = dt.datetime.now(ZoneInfo("America/New_York")).date()
            try:
                bar_is_today = c.index[-1].date() == today_local
            except Exception:
                bar_is_today = False
            prev_close = float(c.iloc[-2]) if (bar_is_today and len(c) > 1) else float(c.iloc[-1])
            if px_live and px_live > 0:
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
            base = 0 if bar_is_today else 1      # so "1 day" means yesterday->now
            def _ret(n):
                i = n + base
                return (price / float(c.iloc[-i]) - 1) * 100 if len(c) > i else None
            spans = {"1d": day, "2d": _ret(2), "3d": _ret(3), "1w": _ret(5),
                     "2w": _ret(10), "3w": _ret(15), "1m": _ret(21)}
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
                         "spans": spans, "struct": s.trend_structure(d),
                         "rets": (c.pct_change().tail(21) * 100).tolist(),
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


def _hot_score(snap):
    """Heat = how much money, how fast, into what kind of trend."""
    rows, spy_m = snap["rows"], snap["spy_month"]
    sec_pct = dict(snap["sectors"])
    sec_rank = {n: i + 1 for i, (n, _) in enumerate(snap["sectors"])}
    scored = []
    for r in rows:
        if r["month"] is None or r["day"] is None:
            continue
        rs = r["month"] - (spy_m or 0.0)
        rv = r.get("rvol") or 1.0
        mf = r.get("money") or 1.0
        sp = sec_pct.get(r["sector"], 0.0)
        rec = r.get("recover") or 0.0
        sm2, sm3 = r.get("smooth2"), r.get("smooth3")
        m2 = r.get("mon2") or 0.0
        m3 = r.get("mon3") or 0.0
        state = (r.get("struct") or {}).get("state")
        heat = ((mf - 1.0) * 10.0
                + r["day"] * 2.0
                + rec * 1.0
                + (m2 * 0.12 if m2 > 0 else m2 * 0.06)
                + {"uptrend": 8.0, "stalling": 2.0, "sideways": 0.0, "basing": 0.0,
                   "uptrend_broken": -4.0, "downtrend": -6.0}.get(state, 0.0)
                + (2.0 if (sm3 or 0) > 0.4 and m3 > 0 else 0.0)
                + sp * 3.0)
        star = (rs > 5 and rv > 1.5 and (r["week"] or 0) > 0 and state == "uptrend")
        scored.append({**r, "rs": rs, "heat": heat, "star": star, "mf": mf,
                       "sec_rank": sec_rank.get(r["sector"])})
    scored.sort(key=lambda x: x["heat"], reverse=True)
    return scored


def hot_ranked(snap=None):
    """The scored + sorted momentum list. Split out of hot_text() so the
    dashboard ranks EXACTLY the same way the Telegram /hot does — one formula,
    one place, no chance of the two drifting apart."""
    snap = snap or snapshot()
    return _hot_score(snap)


def hot_text(snap=None, top_n=8):
    """MOMENTUM ranking — the opposite of /rank. Ranks by where money is moving:
    today's move, unusual volume, 1-month strength vs the market, sector heat.

    Framed honestly: tested on 25k setups, buying strength with a tight stop over
    2-5 days did NOT beat the dip system. The ⭐ flag marks the ONE combination
    that did test well (RS>5% + RVOL>1.5x + near highs = 54% win / +0.184 R)."""
    snap = snap or snapshot()
    scored = _hot_score(snap)
    sec_rank = {n: i + 1 for i, (n, _) in enumerate(snap["sectors"])}
    sec_pct = dict(snap["sectors"])
    _UNUSED = """
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
                + {"uptrend": 8.0, "stalling": 2.0, "sideways": 0.0, "basing": 0.0,
                   "uptrend_broken": -4.0, "downtrend": -6.0,
                   "unclear": 0.0}.get((r.get("struct") or {}).get("state"), 0.0)
                + (2.0 if (sm3 or 0) > 0.4 and m3 > 0 else 0.0)
                + sp * 3.0)
        # ⭐ = the one combination that backtested well, now also requiring the
        # longer trend to be intact (momentum needs a longer hold to pay).
        star = (rs > 5 and rv > 1.5 and (r["week"] or 0) > 0
                and (r.get("struct") or {}).get("state") == "uptrend")
        scored.append({**r, "rs": rs, "heat": heat, "star": star,
                       "mf": mf, "sec_rank": sec_rank.get(r["sector"])})
    """
    del _UNUSED

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
        st = r.get("struct") or {}
        m2, m3, sm3 = r.get("mon2"), r.get("mon3"), r.get("smooth3")
        span = f" ({m2:+.0f}% in 2mo)" if m2 is not None else ""
        SHAPES = {
            "uptrend":        f"🪜 <b>UPTREND</b> — higher highs &amp; higher lows{span}",
            "stalling":       f"⚠️ <b>climb losing steam</b> — higher lows but no new highs{span}",
            "uptrend_broken": f"🔻 <b>uptrend BROKE</b> — price fell under its last higher low{span}",
            "downtrend":      f"❄️ <b>DOWNTREND</b> — lower highs &amp; lower lows{span}",
            "basing":         f"〰️ building a base — lower highs but holding its lows{span}",
            "sideways":       f"〰️ no clear higher-high / higher-low pattern{span}",
        }
        if st.get("state") in SHAPES:
            extra = ""
            if st["state"] == "uptrend" and sm3 is not None and m3 is not None                     and sm3 > 0.4 and m3 > 0:
                extra = " · ✅ 3-month trend confirms it"
            L.append(f"   {SHAPES[st['state']]}{extra}")
        sp = r.get("spans") or {}
        def _f(k):
            v = sp.get(k)
            return f"{v:+.1f}%" if v is not None else "–"
        L.append(f"   2d {_f('2d')} · 1wk {_f('1w')} · 2wk {_f('2w')} · 1mo {_f('1m')} "
                 f"· vs market {r['rs']:+.0f}%")
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


# ---------------------------------------------------------------- timeframes
SPAN_LABEL = {"1d": "1 day", "2d": "2 days", "3d": "3 days", "1w": "1 week",
              "2w": "2 weeks", "3w": "3 weeks", "1m": "1 month"}
SPAN_ALIAS = {"1": "1d", "1d": "1d", "day": "1d", "today": "1d",
              "2": "2d", "2d": "2d", "3": "3d", "3d": "3d",
              "w": "1w", "1w": "1w", "week": "1w", "7d": "1w",
              "2w": "2w", "2week": "2w", "3w": "3w",
              "m": "1m", "1m": "1m", "month": "1m", "30d": "1m"}


def strongest_text(span="1w", top_n=10, snap=None):
    """Strongest movers over ONE chosen window — 1d / 2d / 3d / 1w / 2w / 3w / 1m.
    Lets the same universe be compared across horizons instead of only today."""
    key = SPAN_ALIAS.get((span or "").strip().lower(), "1w")
    snap = snap or snapshot()
    rows = [r for r in snap["rows"] if (r.get("spans") or {}).get(key) is not None]
    if not rows:
        return "No data for that period. Try /strong 1w"
    rows.sort(key=lambda r: r["spans"][key], reverse=True)
    L = [f"🏆 <b>Strongest over {SPAN_LABEL[key]}</b>",
         f"<i>Asked: {_stamp()}</i>", ""]
    for i, r in enumerate(rows[:top_n], 1):
        nm = s.name_for(r["ticker"])
        title = r["ticker"] + (f" · {nm}" if nm else "")
        st = (r.get("struct") or {}).get("state")
        mark = {"uptrend": "🪜", "stalling": "⚠️", "uptrend_broken": "🔻",
                "downtrend": "❄️"}.get(st, "")
        sp = r["spans"]
        others = " · ".join(f"{k} {sp[k]:+.0f}%" for k in ("1d", "1w", "2w", "1m")
                            if k != key and sp.get(k) is not None)
        L.append(f"<b>{i}. {title}</b> — <b>{sp[key]:+.1f}%</b> {mark}")
        L.append(f"   {others}")
    L.append("")
    L.append("<i>🪜 uptrend (higher highs &amp; higher lows) · ⚠️ losing steam · "
             "🔻 uptrend broke · ❄️ downtrend.\n"
             "Compare windows: <code>/strong 1d</code> · <code>2d</code> · "
             "<code>3d</code> · <code>1w</code> · <code>2w</code> · "
             "<code>3w</code> · <code>1m</code></i>")
    return "\n".join(L)


# ------------------------------------------------------- money-flow clusters
def flow_clusters(snap=None, min_corr=0.62, min_size=3, max_groups=4):
    """WHICH STOCKS ARE BEING BOUGHT AS A BASKET RIGHT NOW — discovered from the
    data, not from a fixed sector list.

    Grace's insight: institutions trade correlated BASKETS, and those baskets cut
    straight through the official sectors (a "Technology" list mixes AI hardware,
    legacy IT and software, which trade completely differently). Any hand-written
    taxonomy is out of date the moment the market rotates.

    So instead of labelling, we MEASURE: correlate the last ~4 weeks of daily
    returns, then greedily group names that move together AND are moving today.
    The result is whatever the market is actually treating as one trade.
    """
    snap = snap or snapshot()
    rows = [r for r in snap["rows"]
            if r.get("rets") and len([x for x in r["rets"] if x == x]) >= 12]
    if len(rows) < 6:
        return []
    df = pd.DataFrame({r["ticker"]: pd.Series(r["rets"]).reset_index(drop=True)
                       for r in rows}).dropna(axis=1, how="any")
    if df.shape[1] < 6:
        return []
    corr = df.corr()
    day = {r["ticker"]: (r.get("day") or 0.0) for r in rows}
    money = {r["ticker"]: (r.get("money") or 1.0) for r in rows}
    sect = {r["ticker"]: (r.get("sector") or "") for r in rows}

    # Seed from the biggest movers (either direction) — that's where flow is.
    # The threshold ADAPTS: a fixed 1% starved the list pre-market, when only a
    # handful of names have moved at all (8 of 158 on a typical morning), so only
    # 2-3 baskets ever appeared. Now it takes roughly the top quarter of today's
    # movement, with a small floor so pure noise never seeds a "basket".
    order = sorted(corr.columns, key=lambda t: abs(day.get(t, 0)), reverse=True)
    moves = sorted((abs(day.get(t, 0)) for t in corr.columns), reverse=True)
    cutoff = max(0.25, moves[max(0, len(moves) // 4 - 1)]) if moves else 0.25
    used, groups = set(), []
    for seed in order:
        if seed in used or abs(day.get(seed, 0)) < cutoff:
            continue
        peers = [t for t in corr.columns
                 if t not in used and t != seed
                 and corr.at[seed, t] >= min_corr
                 and day.get(t, 0) * day.get(seed, 0) > 0]   # same direction
        members = [seed] + peers
        if len(members) < min_size:
            continue
        used.update(members)
        members.sort(key=lambda t: day.get(t, 0), reverse=day.get(seed, 0) > 0)
        avg = sum(day.get(t, 0) for t in members) / len(members)
        mf = sum(money.get(t, 1.0) for t in members) / len(members)
        secs = {}
        for t in members:
            if sect.get(t):
                secs[sect[t]] = secs.get(sect[t], 0) + 1
        groups.append({"members": members, "avg": avg, "money": mf,
                       "sectors": sorted(secs, key=secs.get, reverse=True),
                       "up": avg > 0})
        if len(groups) >= max_groups:
            break
    groups.sort(key=lambda g: abs(g["avg"]), reverse=True)
    return groups


def flow_text(snap=None):
    """The '/flow' report — where money is actually going, as baskets."""
    snap = snap or snapshot()
    gs = flow_clusters(snap)
    L = [f"🧲 <b>Money Flow — what's moving as a basket</b>",
         f"<i>Asked: {_stamp()}</i>"]
    if snap.get("session_note"):
        L.append(f"<i>🕒 {snap['session_note']}</i>")
    L.append("")
    if not gs:
        L.append("No clear baskets right now — today's moves look stock-specific "
                 "rather than a coordinated rotation.")
        return "\n".join(L)
    L.append("<i>Groups found by measuring which stocks MOVE TOGETHER (4 weeks of "
             "daily returns), not by any fixed sector list — this is what the big "
             "money is treating as one trade.</i>\n")
    for g in gs:
        arrow = "🟢 BUYING" if g["up"] else "🔴 SELLING"
        L.append(f"{arrow} — avg <b>{g['avg']:+.1f}%</b> today · "
                 f"{g['money']:.1f}× normal money")
        names = []
        for t in g["members"][:8]:
            nm = s.name_for(t)
            names.append(f"{t}")
        L.append(f"   <b>{' · '.join(names)}</b>")
        if g["sectors"]:
            span = ", ".join(g["sectors"][:3])
            note = (" (one sector)" if len(g["sectors"]) == 1
                    else " — cuts across sectors")
            L.append(f"   <i>{span}{note}</i>")
        L.append("")
    L.append("<i>A basket spanning several sectors is the useful case: it means "
             "money is rotating by THEME, which no sector list would have shown "
             "you.</i>")
    return "\n".join(L)


# ------------------------------------------------------------ sector detail
SECTOR_SPANS = [("4h", "4 hours"), ("1d", "today"), ("1w", "1 week"),
                ("2w", "2 weeks"), ("1m", "1 month")]
_sector_multi_cache = {"data": None, "at": None}


def sector_multi(max_age_s=300):
    """Every sector across several horizons: 4h · today · 1w · 2w · 1m.

    'today' is measured from the previous session's close (pre/post-market
    included), matching how the rest of the dashboard defines a day. 4h comes
    from hourly bars so it still says something while a session is running.
    """
    now = dt.datetime.now(dt.timezone.utc)
    c = _sector_multi_cache
    if c["data"] and c["at"] and (now - c["at"]).total_seconds() < max_age_s:
        return c["data"]

    etfs = list(s.SECTOR_ETFS.items())
    syms = [e for _, e in etfs]
    try:
        h = yf.download(syms, period="7d", interval="60m", progress=False,
                        auto_adjust=False, prepost=True, group_by="ticker",
                        threads=True)
    except Exception:
        h = None
    try:
        d = yf.download(syms, period="3mo", interval="1d", progress=False,
                        auto_adjust=False, group_by="ticker", threads=True)
    except Exception:
        d = None

    strength = s.get_sector_strength()
    out = []
    for name, etf in etfs:
        row = {"name": name, "etf": etf, "4h": None, "1d": None,
               "1w": None, "2w": None, "1m": None,
               "strong": strength.get(name, {}).get("strong")}
        dc = None
        try:
            dd = d[etf]
            if isinstance(dd.columns, pd.MultiIndex):
                dd.columns = dd.columns.get_level_values(0)
            dc = dd["Close"].dropna()
        except Exception:
            dc = None
        last = None
        try:
            hc = h[etf]
            if isinstance(hc.columns, pd.MultiIndex):
                hc.columns = hc.columns.get_level_values(0)
            hc = hc["Close"].dropna()
            if len(hc):
                last = float(hc.iloc[-1])
            if len(hc) > 4:
                row["4h"] = (float(hc.iloc[-1]) / float(hc.iloc[-5]) - 1) * 100
        except Exception:
            pass
        if dc is not None and len(dc) > 1:
            px = last if last else float(dc.iloc[-1])
            try:
                today_ny = dt.datetime.now(ZoneInfo("America/New_York")).date()
                bar_today = dc.index[-1].date() == today_ny
            except Exception:
                bar_today = False
            prev = float(dc.iloc[-2]) if (bar_today and len(dc) > 1) else float(dc.iloc[-1])
            row["1d"] = (px / prev - 1) * 100
            base = 0 if bar_today else 1
            for key, n in (("1w", 5), ("2w", 10), ("1m", 21)):
                i = n + base
                if len(dc) > i:
                    row[key] = (px / float(dc.iloc[-i]) - 1) * 100
        out.append(row)
    out.sort(key=lambda r: (r["1d"] is None, -(r["1d"] or 0)))
    _sector_multi_cache.update({"data": out, "at": now})
    return out


def sector_members(snap, sector, n=10, min_share=0.18, min_move=0.2):
    """The stocks doing the most to MOVE a sector today — not just the biggest
    percentage, but the ones carrying real volume behind it, since a 5% pop on
    a fifth of normal volume moves an index far less than a 2% push on 3x.

    Returns only the names that are ACTUALLY contributing: at most `n`, and each
    must pull at least `min_share` of what the leader does and have moved at
    least `min_move`%. A stock sitting at -0.0% is not driving anything, so
    padding the list to a fixed count would just be noise."""
    rows = [r for r in (snap or {}).get("rows", [])
            if (r.get("sector") or "") == sector and r.get("day") is not None]
    if not rows:
        return []
    for r in rows:
        r["_pull"] = (r["day"] or 0) * max(0.4, min(3.0, r.get("money") or 1.0))
    up = sum(1 for r in rows if (r["day"] or 0) > 0) >= len(rows) / 2
    rows.sort(key=lambda r: r["_pull"], reverse=up)
    top = rows[:n]
    if not top:
        return []
    lead = abs(top[0]["_pull"]) or 1.0
    keep = [r for r in top
            if abs(r["_pull"]) >= lead * min_share and abs(r["day"]) >= min_move]
    return keep or top[:3]
