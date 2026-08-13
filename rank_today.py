"""
King Stocks — live "best-qualified setups right now" ranker
-----------------------------------------------------------
This is NOT a prediction of which stocks will go up. It CANNOT do that, and
neither can anyone. What it DOES: score a broad pool of liquid, Trade-Republic-
tradable stocks (your watchlist + all sector-heatmap constituents, ~100 names)
by how well each matches the edge we backtested, and show the best-qualified
candidates *at this moment*. Re-run any time — it re-analyses live.

0-100 FIT SCORE (higher = better match to the dip-in-uptrend edge):
  Trend        25  price above its 50-EMA (uptrend), not over-extended
  Dip / RSI    30  RSI near ~35 (the pullback we buy); penalised if >60 or falling
  Turning up   15  RSI ticking back up (momentum returning)
  Volume       15  above-average buying interest
  Sector       10  its sector is trending up
  Regime        5  broad market (SPY) healthy
Bands: 75+ strong fit · 60-74 good · 45-59 watch · <45 weak.
A name that also meets our strict live-alert trigger is flagged ★ FIRING.

Run:  python rank_today.py
"""

import datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
import scanner as s
import events as ev
import news as nw
import alpaca_data as alp

TOP_N = 10


def _score_100(price, ema_val, rsi_now, rsi_prev, vol_ratio, sec_strong, healthy,
               vwap_state=None, rel_strength=None, support_bonus=0.0,
               support_tag=None):
    """Score = validated dip-in-uptrend CORE (0-80) + quality ENHANCERS (0-20).

    CORE (the edge that worked): Trend 22 · Dip/RSI 30 · Turning-up 14 · Volume 8
    · Sector 3 · Regime 3.
    ENHANCERS (add-only — never knock a good dip out, just lift the excellent
    ones): At-tested-support +9 · Above-VWAP +6 · Rel.strength +5.  Weights set
    from the backtest: support was the strongest factor, VWAP helped, relative
    strength barely moved outcomes.
    Adjustments are smooth (no cliffs): an over-extended RSI is trimmed, and a
    below-trend price gets a GRADUAL counter-trend haircut (bigger the further
    below), so scores don't jump when price grazes the trend line."""
    dist = (price / ema_val - 1) * 100 if ema_val else 0.0
    reasons = []

    # ---------------- CORE edge (0-80) ----------------
    # Trend (0-22): reward uptrend, fade as it gets over-extended above the EMA.
    if price > ema_val:
        trend = max(9.0, min(22.0, 22.0 - max(0.0, dist - 6.0) * 1.4))
        reasons.append("uptrend")
    else:
        trend = max(0.0, min(9.0, 9.0 + dist))     # dist is negative here
        reasons.append("below trend")

    # Dip / RSI zone (0-30): peaks at RSI ~35, fades for extended/falling-knife.
    rsi_s = max(0.0, 30.0 - abs(rsi_now - 35.0) * 1.2)
    if 30 <= rsi_now <= 45:
        reasons.append("dip zone")
    elif rsi_now > 60:
        reasons.append("extended")

    # RSI turning back up (0-14).
    if rsi_now > rsi_prev:
        turn = min(14.0, (rsi_now - rsi_prev) * 3.0)
        reasons.append("turning up")
    else:
        turn = 0.0

    # Volume (0-8): above-average buying interest confirms the bounce.
    vol = min(8.0, max(0.0, (vol_ratio - 0.8) * 8.0))
    if vol_ratio >= 1.3:
        reasons.append(f"vol {vol_ratio:.1f}x")

    # Sector (0-3) and market regime (0-3).
    if sec_strong is True:
        sec = 3.0; reasons.append("sector↑")
    elif sec_strong is False:
        sec = 0.0
    else:
        sec = 1.5
    reg = 3.0 if healthy else 0.0

    core = trend + rsi_s + turn + vol + sec + reg          # up to 80

    # -------------- ENHANCERS (0-20, add-only) --------------
    # At a TESTED support floor (0-9): STRONGEST factor in the backtest. Bonus +
    # timeframe tag precomputed by scanner.support_summary. Away = 0 (no penalty).
    supp = min(9.0, support_bonus or 0.0)
    if support_tag:
        reasons.append(support_tag)

    # VWAP state (0-6): the backtest showed the edge is being AT the VWAP line
    # from above (a "bounce", +0.16R / 57% win), NOT merely above it. Stretched,
    # broken-below and chop were all losers → 0 bonus + a warning tag.
    if vwap_state == "bounce":
        vwap_s = 6.0; reasons.append("🎯 bounce at VWAP")
    elif vwap_state == "above":
        vwap_s = 2.0; reasons.append("above VWAP")
    elif vwap_state == "far_above":
        vwap_s = 0.5; reasons.append("stretched above VWAP")
    elif vwap_state == "broke":
        vwap_s = 0.0; reasons.append("⚠️ broke below VWAP")
    elif vwap_state == "chop":
        vwap_s = 0.0; reasons.append("⚠️ chopping across VWAP")
    elif vwap_state == "below":
        vwap_s = 0.0; reasons.append("below VWAP")
    else:
        vwap_s = 2.0

    # Relative strength (0-5): small weight — it barely moved outcomes in the test.
    if rel_strength is None:
        rs = 2.0
    elif rel_strength >= 5:
        rs = 5.0; reasons.append("market leader")
    elif rel_strength >= 0:
        rs = 3.0
    elif rel_strength >= -5:
        rs = 1.0
    else:
        rs = 0.0; reasons.append("lagging market")

    total = core + supp + vwap_s + rs

    # -------------- smooth honesty adjustments --------------
    if rsi_now > 60:
        total -= (rsi_now - 60) * 1.8              # too extended: the dip passed
    if price < ema_val and ema_val:
        below = (ema_val - price) / ema_val * 100.0
        total *= (1.0 - min(0.15, below * 0.03))   # gradual counter-trend haircut
        reasons.append("counter-trend risk")

    parts = {"Trend": (trend, 22), "Dip/RSI": (rsi_s, 30), "Turning up": (turn, 14),
             "Volume": (vol, 8), "Sector": (sec, 3), "Regime": (reg, 3),
             "Support +": (supp, 9), "VWAP +": (vwap_s, 6), "Rel.str +": (rs, 5)}

    return int(round(max(0.0, min(100.0, total)))), reasons, parts


def _score_from_df(ticker, df, healthy, ctx=None, rt_price=None):
    """Compute the fit row for one ticker from its already-downloaded frame.
    `ctx` is the daily VWAP/support/rel-strength bundle (scanner.daily_context).
    `rt_price` (Alpaca real-time) overrides the latest close so the whole score
    reflects NOW instead of Yahoo's ~15-min-delayed last candle."""
    if df is None or len(df) < s.EMA_TREND + 5:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    if "Close" not in df or df["Close"].dropna().empty:
        return None

    df = df.dropna(subset=["Close"])
    realtime = False
    if rt_price and rt_price > 0:
        df = df.copy()
        df.iloc[-1, df.columns.get_loc("Close")] = float(rt_price)
        realtime = True
    ema_ser = s.ema(df["Close"], s.EMA_TREND)
    rsi_ser = s.rsi(df["Close"], s.RSI_LEN)
    price    = float(df["Close"].iloc[-1])
    ema_val  = float(ema_ser.iloc[-1])
    rsi_now  = float(rsi_ser.iloc[-1])
    rsi_prev = float(rsi_ser.iloc[-2])

    try:
        recent_vol = float(df["Volume"].iloc[-21:-1].mean())
        vol_ratio = float(df["Volume"].iloc[-1]) / recent_vol if recent_vol > 0 else 1.0
    except Exception:
        vol_ratio = 1.0

    ctx = ctx or {}
    sec_strong = s.sector_is_strong(ticker)
    score, reasons, parts = _score_100(
        price, ema_val, rsi_now, rsi_prev, vol_ratio, sec_strong, healthy,
        vwap_state=ctx.get("vwap_state"), rel_strength=ctx.get("rel_strength"),
        support_bonus=ctx.get("support_bonus", 0.0), support_tag=ctx.get("support_tag"))
    firing = bool(price > ema_val and rsi_prev <= s.RSI_OVERSOLD and rsi_now > rsi_prev)

    # ATR-based stop → 1%-rule position size. Prefer DAILY ATR (realistic for a
    # swing hold); fall back to the 15m ATR only if daily isn't available.
    stop = stop_pct = risk = None
    try:
        atr_val = ctx.get("atr_daily")
        if not atr_val:
            atr_val = float(s.atr(df, s.ATR_LEN).iloc[-1])
        stop = price - s.ATR_STOP_MULT * atr_val
        stop_pct = (stop - price) / price * 100.0
        risk = s.risk_position(price, s.ticker_currency(ticker), stop_pct)
    except Exception:
        pass

    try:
        ts = df.index[-1]
        as_of = ts.tz_convert("Europe/Berlin") if ts.tzinfo else ts
    except Exception:
        as_of = None

    return {
        "ticker": ticker, "score": score, "price": price,
        "currency": s.ticker_currency(ticker), "rsi": rsi_now,
        "vol_ratio": vol_ratio, "uptrend": price > ema_val,
        "sector_strong": sec_strong, "firing": firing,
        "as_of": as_of, "reasons": reasons, "parts": parts, "realtime": realtime,
        "stop": stop, "stop_pct": stop_pct, "risk": risk,
        "vwap": ctx.get("vwap"), "above_vwap": ctx.get("above_vwap"),
        "vwap_state": ctx.get("vwap_state"), "vwap_note": ctx.get("vwap_note"),
        "support_levels": ctx.get("support_levels"),
        "support_tier": ctx.get("support_tier"), "support_tag": ctx.get("support_tag"),
        "support_dist": ctx.get("support_dist"), "support_touches": ctx.get("support_touches"),
        "rel_strength": ctx.get("rel_strength"),
    }


def _fmt_sup(lvl_dict, cur):
    """Format one support level: '342.35 USD · 3.4% below · tested 5×'."""
    if not lvl_dict:
        return "none nearby"
    t = lvl_dict["touches"]
    strength = f"tested {t}×" if t > 1 else "1 touch (weak)"
    return f"{lvl_dict['level']:.2f} {cur} · {lvl_dict['dist']:.1f}% below · {strength}"


def _support_line(r) -> str:
    """Plain-language multi-timeframe support + VWAP + leader status."""
    cur = r["currency"]
    bits = []
    sl = r.get("support_levels") or {}
    sh, md, lg = sl.get("short"), sl.get("medium"), sl.get("long")
    if sh or md or lg:
        # headline: which timeframe's tested floor the price is AT (if any)
        tag = r.get("support_tag")
        if tag and tag.startswith("at ") and "weak" not in tag:
            bits.append(f"🧱 <b>Support: 🟢 {tag}</b>")
        elif tag and tag.startswith("near"):
            bits.append(f"🧱 <b>Support: 🟡 {tag}</b>")
        elif tag:
            bits.append(f"🧱 <b>Support: {tag}</b>")
        else:
            bits.append("🧱 <b>Support:</b> not at a tested floor right now")
        bits.append(f"   • Short-term (live, intraday): {_fmt_sup(sh, cur)}")
        bits.append(f"   • Medium-term (≈6mo, daily):  {_fmt_sup(md, cur)}")
        bits.append(f"   • Long-term (≈1yr, weekly):   {_fmt_sup(lg, cur)}")
    st = r.get("vwap_state")
    vd = r.get("vwap_dist")
    dtxt = f" ({vd:+.1f}%)" if vd is not None else ""
    if st == "bounce":
        bits.append(f"🎯 <b>Bounce at VWAP</b>{dtxt} — pulled back to fair value from "
                    "above; prime dip-buy zone (best-performing setup)")
    elif st == "above":
        bits.append(f"💧 Above VWAP{dtxt} — buyers in control (but not at the line)")
    elif st == "far_above":
        bits.append(f"💧 Stretched above VWAP{dtxt} — rubber band; snap-back risk")
    elif st == "broke":
        bits.append(f"⚠️ <b>Broke below VWAP</b>{dtxt} — trend may have broken. "
                    "AVOID even if RSI looks oversold.")
    elif st == "chop":
        bits.append("⚠️ <b>Chopping across VWAP</b> — directionless; likely to hit "
                    "your stop. Best avoided.")
    elif st == "below":
        bits.append(f"💧 Below VWAP{dtxt} — recent buyers underwater (caution)")
    rsx = r.get("rel_strength")
    if rsx is not None:
        if rsx >= 5:
            bits.append(f"🏆 Leader — beating the market by {rsx:+.0f}% (1 month)")
        elif rsx >= 0:
            bits.append(f"↗️ Slightly beating the market ({rsx:+.0f}%, 1 month)")
        else:
            bits.append(f"🐌 Lagging the market ({rsx:+.0f}%, 1 month)")
    return ("\n" + "\n".join(bits)) if bits else ""


def _size_line(r) -> str:
    """1%-rule position size + ATR stop/target, in plain terms."""
    cur = r["currency"]
    stop, stop_pct, rp = r.get("stop"), r.get("stop_pct"), r.get("risk")
    if stop is None or stop_pct is None:
        return ""
    rr = s.active_rr()
    target = r["price"] + rr * (r["price"] - stop)
    tgt_pct = (target - r["price"]) / r["price"] * 100
    out = (f"\n🛑 Stop {stop:.2f} {cur} ({stop_pct:+.1f}%) · "
           f"🎯 Target {target:.2f} {cur} ({tgt_pct:+.1f}%, {rr:g}R)"
           f"\n⏱ <b>{s.load_mode()} mode</b> — aim to exit within {s.mode_horizon()} "
           f"(/mode to switch)")
    if rp:
        cap = " (capped — no leverage)" if rp.get("capped") else ""
        out += (f"\n💰 Buy ≈ <b>€{rp['pos_val']:.0f}</b> (~{rp['shares']:.1f} shares){cap}\n"
                f"   → if the stop hits you lose only <b>€{rp['risk_eur']:.0f}</b> "
                f"({rp['risk_pct']:.0f}% of your €{rp['account']:.0f})")
    return out


def _analyst_line(ticker: str, price: float, cur: str) -> str:
    """Wall-St. consensus rating + median price target from Finnhub (US/NA + a
    free key). Shown as longer-horizon CONTEXT, never folded into the score.
    Empty if no key / non-US / unavailable."""
    try:
        import finnhub_data as fh
    except Exception:
        return ""
    if not fh.has_key():
        return ""
    bits = []
    try:
        rec = fh.recommendation(ticker)
        if rec:
            bits.append(f"consensus <b>{rec['label']}</b> "
                        f"({rec['buy']} buy · {rec['hold']} hold · {rec['sell']} sell)")
    except Exception:
        pass
    try:
        pt = fh.price_target(ticker)
        med = pt.get("median") if pt else None
        if med:
            up = (med / price - 1) * 100 if price else None
            uptxt = f" ({up:+.0f}% vs now)" if up is not None else ""
            bits.append(f"median target <b>{med:.2f} {cur}</b>{uptxt}")
    except Exception:
        pass
    if not bits:
        return ""
    return ("\n🎯 <b>Analysts:</b> " + " · ".join(bits) +
            "\n   <i>Wall-St. consensus — a longer-horizon sanity check, not a "
            "day-trade trigger; the beat/miss reaction is unpredictable.</i>")


def score_one(ticker: str, healthy=None) -> str:
    """Detailed 0-100 breakdown for a single ticker (any symbol you type)."""
    ticker = ticker.strip().upper()
    if not ticker:
        return "Usage: /score SYM   (e.g. /score NVDA or /score SAP.DE)"
    s.refresh_caches()   # manual command → refetch market/sector/FX (not session-cached)
    if healthy is None:
        healthy = s.market_is_healthy()
    try:
        df = yf.download(ticker, period=s.LOOKBACK, interval=s.INTERVAL,
                         progress=False, auto_adjust=False, prepost=s.EXTENDED_HOURS)
    except Exception:
        df = None
    # daily frame for VWAP / support / relative strength
    try:
        rtp = alp.latest_prices([ticker]).get(ticker)
    except Exception:
        rtp = None
    r0 = _score_from_df(ticker, df, healthy, rt_price=rtp)
    ctx = None; dd = None
    if r0 is not None:
        try:
            # 2y — same range /rank uses, so /score and /rank give identical
            # support tiers (short 2mo, medium 9mo, long weekly-2y).
            dd = yf.download(ticker, period="2y", interval="1d",
                             progress=False, auto_adjust=False)
            ctx = s.daily_context(ticker, dd, r0["price"], intraday=df)
        except Exception:
            ctx = None
    r = _score_from_df(ticker, df, healthy, ctx=ctx, rt_price=rtp) if ctx else r0
    if not r:
        return (f"❌ No usable data for <b>{ticker}</b>. Check the symbol "
                f"(EU names need a suffix like .DE .AS .PA .L .F). "
                f"Tip: /find &lt;company name&gt; to look it up.")

    nm = s.name_for(ticker)
    title = ticker + (f" · {nm}" if nm else "")
    cur = r["currency"]
    breakdown = "\n".join(
        f"   {k:<12} {v:>4.0f}/{mx}" for k, (v, mx) in r["parts"].items()
    )
    star = " ★FIRING" if r["firing"] else ""
    fresh = _freshness_line([r])

    # Earnings gap-risk warning + a rough news lean (both best-effort).
    try:
        earn = ev.earnings_warning(ticker)
    except Exception:
        earn = ""
    try:
        news_line = nw.stock_news_brief(ticker)
    except Exception:
        news_line = ""

    # Earnings break-even lens (anchored VWAP from last report). Context, not
    # scored — its clearest signal is 'below = post-earnings move failed, avoid'.
    eline = ""
    try:
        led = s.last_earnings_date(ticker)
        if led is not None and dd is not None:
            es = s.earnings_avwap_signal(dd, r["price"], led)
            if es["state"] != "unknown" and es.get("avwap"):
                pre = {"above": "🏦", "bounce": "🏦🎯", "below": "🏦⚠️"}.get(es["state"], "🏦")
                eline = (f"\n{pre} <b>Earnings break-even</b> {es['avwap']:.2f} {cur} "
                         f"({es['dist']:+.1f}%) — {es['note']}")
    except Exception:
        eline = ""

    # Assemble as clearly separated blocks (blank line between each) so /score
    # reads as neatly as /rank instead of one dense wall of text.
    head = [f"{_volatility_banner()}🔎 <b>{title}</b> — "
            f"<b>{r['score']}/100</b> {_band(r['score'])}{star}"]
    if fresh:
        head.append(fresh)
    snapshot = (f"Price <b>{r['price']:.2f} {cur}</b> · RSI {r['rsi']:.0f} · "
                f"{'uptrend' if r['uptrend'] else 'below trend'} · "
                f"vol {r['vol_ratio']:.1f}×")
    breakdown_block = (f"<b>Score breakdown</b>\n<pre>{breakdown}</pre>\n"
                       f"{(' · '.join(r['reasons']))}")

    analyst = _analyst_line(ticker, r["price"], cur)
    sections = ["\n".join(head), snapshot]
    for blk in (_support_line(r), _size_line(r), earn, news_line, analyst, eline):
        blk = blk.strip("\n") if blk else ""
        if blk:
            sections.append(blk)
    sections += [breakdown_block]
    return "\n\n".join(sections)


def rank(top_n: int = TOP_N, healthy=None):
    """Batch-scan the wide universe and return the top_n by 0-100 fit score."""
    s.refresh_caches()   # manual command → refetch market/sector/FX (not session-cached)
    if healthy is None:
        healthy = s.market_is_healthy()
    universe = s.rank_universe()
    # 15m frame for the intraday dip/turn signal…
    try:
        data = yf.download(universe, period=s.LOOKBACK, interval=s.INTERVAL,
                           progress=False, auto_adjust=False,
                           prepost=s.EXTENDED_HOURS, group_by="ticker", threads=True)
    except Exception:
        data = None
    # …and a 2-year daily frame for VWAP + weekly support + rel. strength.
    # (2y = plenty of weekly pivots, current regime; keeps the 126-name batch light.)
    try:
        ddata = yf.download(universe, period="2y", interval="1d",
                            progress=False, auto_adjust=False,
                            group_by="ticker", threads=True)
    except Exception:
        ddata = None

    # Real-time US prices (Alpaca IEX) so the score reflects NOW, not the ~15-min
    # delayed last candle. One batched request; empty dict if no keys → yfinance.
    try:
        rt = alp.latest_prices(universe)
    except Exception:
        rt = {}

    rows = []
    for t in universe:
        try:
            df = data[t] if data is not None else None
        except Exception:
            df = None
        rtp = rt.get(t)
        r0 = _score_from_df(t, df, healthy, rt_price=rtp)  # need price for context
        price = r0["price"] if r0 else None
        ctx = None
        if price is not None and ddata is not None:
            try:
                ctx = s.daily_context(t, ddata[t], price, intraday=df)
            except Exception:
                ctx = None
        r = _score_from_df(t, df, healthy, ctx=ctx, rt_price=rtp) if ctx else r0
        if r:
            rows.append(r)
    rows.sort(key=lambda x: (x["score"], x["vol_ratio"]), reverse=True)
    top = rows[:top_n]
    # News lean only for the handful actually shown (keeps /rank fast). Shown as a
    # context tag — NOT folded into the score (keyword sentiment is too rough, and
    # news direction doesn't reliably predict the next move).
    for r in top:
        try:
            r["news_emoji"], r["news_word"] = nw.stock_lean(r["ticker"])
        except Exception:
            r["news_emoji"], r["news_word"] = None, None
    return top


def _stamp() -> str:
    now = dt.datetime.now(ZoneInfo("Europe/Berlin"))
    return now.strftime("%d %b %Y, %H:%M CET")


def _us_session() -> str:
    """US market phase now: 'regular', 'pre', 'post', or 'closed'."""
    try:
        ny = dt.datetime.now(ZoneInfo("America/New_York"))
        if ny.weekday() >= 5:
            return "closed"
        m = ny.hour * 60 + ny.minute
        if (9 * 60 + 30) <= m < (16 * 60):
            return "regular"
        if (4 * 60) <= m < (9 * 60 + 30):
            return "pre"
        if (16 * 60) <= m < (20 * 60):
            return "post"
        return "closed"
    except Exception:
        return "regular"


def _freshness_line(rows) -> str:
    """Describe how current the data is, based on the newest candle used."""
    stamps = [r["as_of"] for r in rows if r.get("as_of") is not None]
    if not stamps:
        return ""
    newest = max(stamps)
    age_min = (dt.datetime.now(ZoneInfo("Europe/Berlin")) - newest).total_seconds() / 60
    when = newest.strftime("%d %b %H:%M CET")
    phase = _us_session()
    label = {
        "regular": "US market OPEN",
        "pre": "US pre-market (thin volume)",
        "post": "US after-hours (thin volume)",
        "closed": "US market CLOSED",
    }[phase]
    rt_active = any(r.get("realtime") for r in rows)
    if rt_active:
        # Alpaca overrides the price → current, even if the yfinance candle is old.
        tail = " · 🟢 <b>US prices real-time</b> (Alpaca)"
    elif age_min <= 25:
        tail = " · live (~15-min delayed)"
    elif age_min < 120:
        tail = f" · ⚠️ candle ~{age_min:.0f} min old (Yahoo feed lagging)"
    else:
        tail = f" · ⚠️ ~{age_min/60:.1f}h old (last available price)"
    # EU/UK names only trade ~09:00–17:30 CET → outside that they're at last close.
    non_us = any((r.get("currency") or "USD") != "USD" for r in rows)
    if non_us and not _eu_market_open():
        tail += " · 🌍 <b>EU/UK names at last close</b> (not live)"
    return f"📈 Data as of <b>{when}</b> · {label}{tail}"


def _eu_market_open() -> bool:
    """Rough check: are European exchanges open (Mon–Fri ~09:00–17:30 CET)?"""
    now = dt.datetime.now(ZoneInfo("Europe/Berlin"))
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return (9 * 60) <= mins <= (17 * 60 + 30)


def _volatility_banner() -> str:
    """Warn when scores are less reliable: a 🔴 macro event today, or the first
    30 min after the US open (whippy, unsettled prices)."""
    bits = []
    try:
        hi = ev.high_impact_today()
        if hi:
            bits.append(f"🔴 <b>{hi} today</b> — the market is digesting it.")
    except Exception:
        pass
    try:
        if s.in_us_opening():
            bits.append("⏱ <b>First 30 min after the US open</b> — whippy, prices not settled.")
    except Exception:
        pass
    if not bits:
        return ""
    return ("⚠️ <b>High-volatility window — scores are less reliable right now.</b>\n"
            + "\n".join(bits)
            + "\n<i>Better to wait ~30–60 min after the open before acting on a score.</i>\n\n")


def _band(score: int) -> str:
    # Just the colored dot — the score number already conveys the strength.
    if score >= 60:
        return "🟢"
    if score >= 45:
        return "🟡"
    return "⚪"


def format_ranking(rows, healthy: bool = True) -> str:
    lines = []
    banner = _volatility_banner()
    if banner:
        lines.append(banner.rstrip())
    lines += ["👑 <b>King Stocks — best-qualified setups now</b>",
              f"<i>Asked: {_stamp()} · scanned {len(s.rank_universe())} stocks</i>"]
    fresh = _freshness_line(rows)
    if fresh:
        lines.append(fresh)
    if not healthy:
        lines.append("\n⚠️ <b>Market regime WEAK</b> (SPY below its 50-day avg). "
                     "Dip-buys are lower-odds now — these are relative rankings only.")
    lines.append("")
    for i, r in enumerate(rows, 1):
        star = " ★" if r["firing"] else ""
        nm = s.name_for(r["ticker"])
        title = f"{r['ticker']}" + (f" · {nm}" if nm else "")
        # Only the tags that carry real signal (backtest): at-support, VWAP
        # bounce, the two VWAP warnings, and the news lean (🔴 negative = hold-off
        # risk, 🟢 positive = thesis support — but a fresh pop may already be in).
        tags = []
        stag = r.get("support_tag") or ""
        if stag and "weak" not in stag and (stag.startswith("at ") or stag.startswith("near")):
            tags.append(f"🧱 {r.get('support_tier','')} support")
        st = r.get("vwap_state")
        if st == "bounce":
            tags.append("🎯 VWAP bounce")
        elif st == "broke":
            tags.append("⚠️ broke VWAP")
        elif st == "chop":
            tags.append("⚠️ VWAP chop")
        if r.get("news_emoji") == "🔴":
            tags.append("📰 negative news")
        elif r.get("news_emoji") == "🟢":
            tags.append("📰 positive news")
        tag_line = ("\n   " + " · ".join(tags)) if tags else ""
        rp = r.get("risk")
        size_line = ""
        if rp:
            size_line = (f"\n   💰 ≈€{rp['pos_val']:.0f} · risk €{rp['risk_eur']:.0f} "
                         f"(stop {r['stop_pct']:+.1f}%)")
        lines.append(
            f"<b>{i}. {title}</b> — <b>{r['score']}/100</b> {_band(r['score'])}{star}\n"
            f"   {r['price']:.2f} {r['currency']} · RSI {r['rsi']:.0f}"
            f"{tag_line}{size_line}"
        )
        lines.append("")   # blank line between setups for readability
    lines.append("<i>🎯 bounce &amp; 🧱 support = higher quality · ⚠️ = avoid · "
                 "📰 = news lean (context, not scored) · ★ = live trigger. "
                 "💰 risks ~1% per trade.</i>")
    return "\n".join(lines)


def run(send: bool = False, top_n: int = TOP_N):
    healthy = s.market_is_healthy()
    rows = rank(top_n, healthy=healthy)
    text = format_ranking(rows, healthy)
    if send:
        s.send_telegram(text)
    # console-safe print
    print(text.replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", "")
              .encode("ascii", "replace").decode("ascii"))
    return rows


if __name__ == "__main__":
    run(send=False)
