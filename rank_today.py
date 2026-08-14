"""
King Stocks — live "best-qualified setups right now" ranker
-----------------------------------------------------------
This is NOT a prediction of which stocks will go up. It CANNOT do that, and
neither can anyone. What it DOES: score a broad pool of liquid, Trade-Republic-
tradable stocks (your watchlist + all sector-heatmap constituents, ~100 names)
by how well each matches the edge we backtested, and show the best-qualified
candidates *at this moment*. Re-run any time — it re-analyses live.

0-100 FIT SCORE. Every weight below was set by backtest, not opinion — see
_score_100 for the numbers behind each one:
  Dip / RSI    30  deep oversold is the real signal (RSI<30 = 56% win / +0.174 R)
  Room to run  20  space to the next resistance (>=2R = 56% win / +0.179 R)
  Support      20  at a tested floor (53% win / +0.109 R vs 50% / +0.048 away)
  Trend         8  price vs its ~2-day trend line   (measured ~0 — small weight)
  Turning up    4  RSI ticking back up              (measured ~0)
  Sector        3  its sector trending up           (~0 alone…)
  Sector fit    3  …but +0.045 R when it sits UNDER support
  Regime        3  broad market (SPY) healthy
  VWAP          3  mainly for its AVOID warnings    (measured ~0)
  Volume        2  no monotonic signal              (measured ~0)
  Rel. strength 1  measured ~0 three separate ways  (display cue only)
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
               support_tag=None, upside=None, trend_heat=None):
    """0-100 entry-quality score. EVERY weight is set from a backtest.

    Measured effect on forward R (spread between the best and worst bucket of
    each factor), all on 5 years of US large-caps, 1.5xATR stop / 1.5R target:
      Room to run  +0.126   >=2R to resistance 56% win /+0.179 vs <2R 50% /+0.053
      RSI < 30     +0.102   56% win / +0.174 R  (the mid "dip zone" is ~baseline)
      Support      +0.061   at a tested floor 53% /+0.109 vs away 50% /+0.048
      Sector fit   +0.045   only WHEN the rising sector sits under support
      Trend        ~0       above vs below the trend line is a coin flip
      Turning up   ~0       still-falling scored marginally BETTER
      Volume       ~0       no monotonic signal; heavy volume was the wrong way
      Rel.strength ~0       null on 21-day, 5-day, and leader/laggard splits
      VWAP         ~0       kept for its AVOID states (broke / chop) only

    Honest caveat that has survived every test: this ranks ENTRY QUALITY, it does
    not forecast profit. The whole score spans roughly 50%-59% win rates. Stops
    and 1% position sizing still matter more to the P&L than picking #1 over #5.
    Enhancers are add-only — they lift good setups, never knock one out."""
    dist = (price / ema_val - 1) * 100 if ema_val else 0.0
    reasons = []

    # ---------------- CORE edge (0-63) ----------------
    # Trend (0-12): CUT from 20 on 2026-08-13. A 17329-setup test found the trend
    # filter has NO measured edge at any horizon: above the 50-day trend 52% win /
    # +0.040 R vs BELOW it 53% / +0.044 at a 2-day hold (and +0.064 vs +0.080 at
    # 5 days). Same result for the fast ~2-day trend line. It was the 2nd-largest
    # component of the score while predicting nothing, so its weight now reflects
    # that. Kept (not removed) because a collapsing stock is still worse to hold.
    if price > ema_val:
        trend = max(3.0, min(8.0, 8.0 - max(0.0, dist - 6.0) * 0.6))
        reasons.append("above short-term trend")
    else:
        trend = max(0.0, min(3.0, 3.0 + dist * 0.4))   # dist is negative here
        reasons.append("below short-term trend")

    # Dip / RSI (0-37). RE-SHAPED 2026-08-13 from a 23744-setup test: the edge is
    # concentrated in DEEP oversold, not the mid dip zone the old curve peaked on.
    #   RSI <30  56% win / +0.174 R  (+0.102 vs baseline)  <- the real signal
    #   RSI 30-45 ~51-53% / +0.070-0.080 (≈ baseline)
    #   RSI 45-60 / 60+  slightly BELOW baseline
    # So: full credit under 30, a moderate slope to 45, then it fades away.
    if rsi_now <= 30:
        rsi_s = 30.0
    elif rsi_now <= 45:
        rsi_s = 30.0 - (rsi_now - 30.0) / 15.0 * 12.0      # 30 -> 18
    elif rsi_now <= 60:
        rsi_s = 18.0 - (rsi_now - 45.0) / 15.0 * 13.0      # 18 -> 5
    else:
        rsi_s = max(0.0, 5.0 - (rsi_now - 60.0) * 0.35)
    if 30 <= rsi_now <= 45:
        reasons.append("dip zone")
    elif rsi_now > 60:
        reasons.append("extended")

    # RSI turning back up (0-6, CUT from 12). Tested: turning up +0.068 R vs still
    # FALLING +0.076 — no edge, if anything backwards. Kept small because entering
    # while a stock is still dropping is bad practice regardless of the average.
    if rsi_now > rsi_prev:
        turn = min(4.0, (rsi_now - rsi_prev) * 1.2)
        reasons.append("turning up")
    else:
        turn = 0.0

    # Volume (0-3, CUT from 7). Tested across 23744 setups: NO monotonic signal
    # (0-0.7x +0.086 · 1.0-1.3x +0.061 · 1.3-1.8x +0.058) — rewarding heavy volume
    # was, if anything, the wrong direction. Nearly zero weight now.
    vol = min(2.0, max(0.0, (vol_ratio - 0.8) * 2.0))
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

    core = trend + rsi_s + turn + vol + sec + reg          # up to 64

    # -------------- ENHANCERS (0-33, add-only) --------------
    # At a TESTED support floor (0-24). REDUCED from 28: on the same 23744-setup
    # sample support's effect (+0.061 R spread) is real but MODEST — and there are
    # three tiers, none right every time. It is still the strongest broad factor,
    # just no longer allowed to dominate the score on its own.
    supp = min(18.0, support_bonus or 0.0)
    if support_tag:
        reasons.append(support_tag)

    # VWAP state (0-6): the backtest showed the edge is being AT the VWAP line
    # from above (a "bounce", +0.16R / 57% win), NOT merely above it. Stretched,
    # broken-below and chop were all losers → 0 bonus + a warning tag.
    if vwap_state == "bounce":
        vwap_s = 3.0; reasons.append("🎯 bounce at VWAP")
    elif vwap_state == "above":
        vwap_s = 1.5; reasons.append("above VWAP")
    elif vwap_state == "far_above":
        vwap_s = 0.5; reasons.append("stretched above VWAP")
    elif vwap_state == "broke":
        vwap_s = 0.0; reasons.append("⚠️ broke below VWAP")
    elif vwap_state == "chop":
        vwap_s = 0.0; reasons.append("⚠️ chopping across VWAP")
    elif vwap_state == "below":
        vwap_s = 0.0; reasons.append("below VWAP")
    else:
        vwap_s = 1.5

    # Relative strength (0-1): measured ≈ ZERO three separate times (21-day RS,
    # 5-day RS vs SPY, and leader/laggard splits). Kept only as a display cue.
    if rel_strength is None:
        rs = 0.5
    elif rel_strength >= 5:
        rs = 1.0; reasons.append("market leader")
    elif rel_strength >= 0:
        rs = 0.75
    elif rel_strength >= -5:
        rs = 0.25
    else:
        rs = 0.0; reasons.append("lagging market")

    # RECENT-WEEK STRENGTH (0-3). The ONLY trend variant that tested POSITIVE
    # alongside a dip: week-up + dip 54% win / +0.109 R vs week-down + dip 52% /
    # +0.085. Deliberately small. Everything longer FAILED: dip + month-up 49% /
    # +0.073 (worse than dip + month-down), and a dip inside a STEADY CLIMB was
    # the worst bucket of all (43% win / -0.081 R). So "hot" does NOT earn score
    # here — this bot's edge is mean reversion, and momentum is a separate
    # strategy that lives in /hot.
    th = trend_heat or {}
    wk = th.get("week")
    if wk is None:
        wkpts = 1.5
    elif wk > 0:
        wkpts = 3.0
    else:
        wkpts = 1.0

    # PULLBACK INSIDE A CLIMB (0-8) — Grace's idea, and it holds up once the dip
    # is measured RELATIVE to the stock's own range instead of a fixed RSI level.
    # A clean climber never reaches RSI 45, which is why the first test wrongly
    # said the setup "doesn't exist". Measured properly (27944 setups):
    #   steady climb (LRcorr>0.7) + RSI in its own lower third → 52% win /+0.109 R
    #   steady climb + pullback 1.5-3 ATR                      → 52% win /+0.096 R
    #   steady climb sitting AT its highs (no pullback)        → 49% win /+0.042 ✗
    # Note it is the PULLBACK that pays, not the heat: momentum RANK alone was
    # flat (top 20% +0.049 vs bottom 20% +0.068), so "hot" only counts here when
    # the stock has actually stepped back.
    smooth = th.get("smooth")
    rsi_pct = th.get("rsi_pct")
    pull = th.get("pull_atr")
    climb = 0.0
    if smooth is not None and smooth > 0.7:
        if rsi_pct is not None and rsi_pct < 33:
            climb = 8.0
            reasons.append("🪜 pullback inside a steady climb")
        elif pull is not None and 1.5 <= pull <= 3.0:
            climb = 6.0
            reasons.append("🪜 dip inside a steady climb")
        elif rsi_pct is not None and rsi_pct < 50:
            climb = 3.0
            reasons.append("🪜 steady climb, easing back")
        else:
            climb = 1.0
            reasons.append("🪜 steady climb (but at its highs)")
    elif smooth is not None and smooth > 0.4:
        if rsi_pct is not None and rsi_pct < 40:
            climb = 3.0
            reasons.append("↗️ pullback in a rising trend")

    # ROOM TO RUN (0-20): distance to the next overhead resistance, in R. The
    # STRONGEST factor measured (15942 setups): >=2R → 56% win / +0.179 R vs
    # <2R → 50% / +0.053, and it rises monotonically (2.5-4R = 59% / +0.218).
    # A dip at support is worthless if a ceiling sits right above the entry.
    room = (upside or {}).get("room_r")
    if room is None:
        space = 10.0                       # unknown → neutral
    elif room >= 2.5:
        space = 18.0; reasons.append(f"🚀 clear run ({min(room,9.9):.1f}R to resistance)")
    elif room >= 2.0:
        space = 16.0; reasons.append(f"room to run ({room:.1f}R)")
    elif room >= 1.5:
        space = 11.0
    elif room >= 1.0:
        space = 7.0
    elif room >= 0.5:
        space = 3.0; reasons.append(f"⚠️ resistance close ({room:.1f}R)")
    else:
        space = 0.0; reasons.append(f"⚠️ capped by resistance ({room:.1f}R above)")

    # Sector ALIGNMENT bonus (0-3). Sector trend on its own is worthless
    # (uptrend +0.059 R vs downtrend +0.078 — no edge, tested on 3937 setups), so
    # it stays at 3 points standalone. But a rising sector UNDER a stock that is
    # at tested support is the best combination measured in this project:
    # at-support + sector-up = +0.151 R / 55% win, vs +0.106 R when the sector is
    # falling. That synergy is worth its own small bonus.
    align = 0.0
    if sec_strong is True and support_tag and support_tag.startswith("at "):
        align = 3.0
        reasons.append("✅ sector rising under support")

    total = core + supp + vwap_s + rs + align + space + wkpts + climb

    # -------------- smooth honesty adjustments --------------
    if rsi_now > 60:
        total -= (rsi_now - 60) * 1.8              # too extended: the dip passed
    if price < ema_val and ema_val:
        below = (ema_val - price) / ema_val * 100.0
        total *= (1.0 - min(0.15, below * 0.03))   # gradual counter-trend haircut
        reasons.append("counter-trend risk")

    parts = {"Trend": (trend, 8), "Dip/RSI": (rsi_s, 30), "Turning up": (turn, 4),
             "Volume": (vol, 2), "Sector": (sec, 3), "Regime": (reg, 3),
             "Room to run": (space, 18), "Support +": (supp, 18),
             "VWAP +": (vwap_s, 3), "Rel.str +": (rs, 1), "Sector fit +": (align, 3),
             "Week trend +": (wkpts, 3), "Climb pullback +": (climb, 8)}

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
        support_bonus=ctx.get("support_bonus", 0.0), support_tag=ctx.get("support_tag"),
        upside=ctx.get("upside"), trend_heat=ctx.get("trend_heat"))
    firing = bool(price > ema_val and rsi_prev <= s.RSI_OVERSOLD and rsi_now > rsi_prev)

    # ATR-based stop → 1%-rule position size. Prefer DAILY ATR (realistic for a
    # swing hold); fall back to the 15m ATR only if daily isn't available.
    stop = stop_pct = risk = None
    try:
        atr_val = ctx.get("atr_daily")
        if not atr_val:
            atr_val = float(s.atr(df, s.ATR_LEN).iloc[-1])
        stop = price - s.active_stop_mult() * atr_val
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
        "range_pos": ctx.get("range_pos"), "range_pct": ctx.get("range_pct"),
        "support_note": ctx.get("support_note"),
        "sector": s.sector_info(ticker), "upside": ctx.get("upside"),
        "trend_heat": ctx.get("trend_heat"),
        "turning_up": rsi_now > rsi_prev,
    }


def _fmt_sup(lvl_dict, cur):
    """One support level: '342.35 USD · 3.4% below (0.7×ATR) · tested 5×'.
    The ATR figure is what actually matters — it says how close that is FOR THIS
    stock, so a 1% gap on a quiet name isn't confused with 1% on a wild one."""
    if not lvl_dict:
        return "none nearby"
    t = lvl_dict["touches"]
    strength = f"tested {t}×" if t > 1 else "1 touch (weak)"
    da = lvl_dict.get("dist_atr")
    atr_txt = f" ({da:.1f}×ATR)" if da is not None else ""
    return (f"{lvl_dict['level']:.2f} {cur} · {lvl_dict['dist']:.1f}% below"
            f"{atr_txt} · {strength}")


def _support_line(r) -> str:
    """Plain-language multi-timeframe support + VWAP + leader status."""
    cur = r["currency"]
    bits = []
    sl = r.get("support_levels") or {}
    # Only show levels that are actually RELEVANT to the price now. Beyond ~5%
    # the backtest edge fades (within 3%: +0.102 R · within 8%: +0.066), and a
    # floor 10% away tells you nothing about today — so it's hidden entirely.
    def _near(lv):
        if not lv:
            return None
        da = lv.get("dist_atr")
        if da is not None:
            return lv if da <= s.SUPPORT_HIDE_ATR else None
        return lv if (lv.get("dist") is not None and lv["dist"] <= 5.0) else None
    sh, md, lg = _near(sl.get("short")), _near(sl.get("medium")), _near(sl.get("long"))
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
        rp = r.get("range_pos")
        if rp is not None:
            if rp <= 40:
                icon, mean = "🟢", "in the lower part — a real pullback"
            elif rp <= 55:
                icon, mean = "🟡", "middle of the range"
            else:
                icon, mean = "🔴", "near the highs — buying here is chasing"
            bits.append(f"📍 {icon} Price at <b>{rp:.0f}%</b> of its recent range — {mean}")
        if sh:
            bits.append(f"   • Short-term (live, intraday): {_fmt_sup(sh, cur)}")
        if md:
            bits.append(f"   • Medium-term (≈6mo, daily):  {_fmt_sup(md, cur)}")
        if lg:
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
    # Trend heat — only shown when the shape is CLEAR. If the chart is choppy or
    # directionless there is nothing useful to say, so we say nothing.
    th = r.get("trend_heat") or {}
    sm, mo, wk = th.get("smooth"), th.get("month"), th.get("week")
    rp = th.get("rsi_pct")
    stc = (th.get("struct") or {})
    state = stc.get("state")
    if state in ("uptrend", "stalling", "uptrend_broken", "downtrend"):
        span = ""
        parts_ = []
        for lbl, v in (("1wk", wk), ("2wk", th.get("wk2")),
                       ("1mo", mo), ("2mo", th.get("mon2"))):
            if v is not None:
                parts_.append(f"{lbl} {v:+.1f}%")
        span = ("\n   " + " · ".join(parts_)) if parts_ else ""
        if state == "uptrend":
            step = ""
            if rp is not None:
                step = (" — <b>and it has stepped back</b> (good spot to join)"
                        if rp < 33 else
                        (" — but it's at the top of its range (wait for a step back)"
                         if rp > 70 else ""))
            bits.append(f"🔥 <b>HOT — uptrend</b> (higher highs &amp; higher lows){span}{step}")
        elif state == "stalling":
            bits.append(f"⚠️ <b>Climb losing steam</b> — still higher lows, but it has "
                        f"stopped making new highs{span}")
        elif state == "uptrend_broken":
            bits.append(f"🔻 <b>Uptrend just BROKE</b> — price fell below its last "
                        f"higher low{span}")
        else:
            bits.append(f"❄️ <b>COLD — downtrend</b> (lower highs &amp; lower lows){span}\n"
                        "   ⚠️ It has been falling for weeks. A green day here is "
                        "usually a short bounce, not a recovery.")
    up = r.get("upside") or {}
    if up.get("room_r") is not None:
        rr, lvl = up["room_r"], up.get("level")
        if lvl:
            if rr >= 2.0:
                bits.append(f"🚀 <b>Room to run</b>: next resistance {lvl:.2f} {cur} "
                            f"({up['pct']:+.1f}%) = <b>{min(rr,9.9):.1f}R</b> of upside "
                            "— target is reachable")
            else:
                bits.append(f"⚠️ <b>Overhead resistance</b> {lvl:.2f} {cur} "
                            f"({up['pct']:+.1f}%) = only <b>{rr:.1f}R</b> — the target "
                            "is blocked before you get there")
        else:
            bits.append("🚀 <b>Room to run</b>: no resistance overhead (clear sky)")
    sec = r.get("sector")
    if sec:
        arrow = "🟢" if sec["strong"] and sec["month"] > 0 else "🔴"
        bits.append(f"{arrow} <b>{sec['name']} sector</b>: {sec['day']:+.1f}% today · "
                    f"{sec['week']:+.1f}% week · {sec['month']:+.1f}% month"
                    f"{' · above its 50-day trend' if sec['strong'] else ' · below its 50-day trend'}")
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
    return "\n🎯 <b>Analysts</b> <i>(Wall Street)</i>: " + " · ".join(bits)


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
        sdist = r.get("support_dist")
        tier = r.get("support_tier", "")
        if stag and "weak" not in stag:
            # Say AT vs NEAR honestly and give the distance — collapsing both to
            # "support" made a floor 4% underneath look like price sitting on it.
            d = f" ({sdist:.1f}% below)" if sdist is not None else ""
            if stag.startswith("at "):
                tags.append(f"🧱 <b>at {tier} support</b>")
            elif stag.startswith("just above"):
                tags.append(f"🧱 just above {tier} support{d}")
            elif stag.startswith("sitting on"):
                tags.append(f"🧱 on {tier} line (not bounced yet)")
            elif stag.startswith("above a"):
                tags.append(f"▫️ above a {tier} level (not a dip)")
            elif stag.startswith("near"):
                tags.append(f"🧱 near {tier} support{d}")
        up = r.get("upside") or {}
        room = up.get("room_r")
        if room is not None:
            if room >= 2.5:
                tags.append(f"🚀 <b>clear run</b> ({min(room,9.9):.1f}R to resistance)")
            elif room >= 2.0:
                tags.append(f"🚀 room to run ({room:.1f}R)")
            elif room < 1.0:
                tags.append(f"⚠️ <b>resistance {room:.1f}R above</b> — target blocked")
        rpos = r.get("range_pos")
        if rpos is not None and rpos > 55:
            tags.append(f"📍 {rpos:.0f}% of range (near highs)")
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

        # The other scoring factors, short but complete, so the ranking itself
        # says WHY a name is here (no need to open /score to decide).
        # Name the SOURCE of each factor — "uptrend" alone read like an RSI thing,
        # but it is price vs its 50-EMA, which is a different signal from RSI.
        # "50-EMA" read like 50 DAYS; it is 50 x 15-min bars ≈ 2 trading days.
        setup = ["📈 above 2-day trend" if r["uptrend"] else "📉 below 2-day trend"]
        if r["rsi"] <= 45:
            setup.append("dip zone")
        elif r["rsi"] > 60:
            setup.append("extended")
        if r.get("turning_up"):
            setup.append("RSI ↗ turning up")
        vr = r.get("vol_ratio")
        if vr:
            setup.append(f"vol {vr:.1f}×")
        rsx = r.get("rel_strength")
        if rsx is not None:
            setup.append(f"vs market {rsx:+.0f}%")
        setup_line = "\n   " + " · ".join(setup)

        # Trend shape — is this dip happening inside a climb, a slide, or chop?
        th = r.get("trend_heat") or {}
        wk, mo, sm = th.get("week"), th.get("month"), th.get("smooth")
        heat_line = ""
        if mo is not None:
            if sm is not None and sm >= 0.75 and mo > 0:
                shape = "🪜 steady climb"
            elif sm is not None and sm <= -0.75 and mo < 0:
                shape = "🔻 steady slide"
            elif mo > 0:
                shape = "↗️ up but choppy"
            else:
                shape = "↘️ down, choppy"
            heat_line = (f"\n   {shape} · week {wk:+.1f}% · month {mo:+.1f}%"
                         if wk is not None else f"\n   {shape} · month {mo:+.1f}%")

        sec = r.get("sector")
        sec_line = ""
        if sec:
            rk, of = sec.get("day_rank"), sec.get("of") or 11
            # 🔥 = top-3 sector TODAY (where money is rotating right now — what
            # matters for a fast trade); 🧊 = bottom-3.
            if rk and rk <= 3:
                heat = f"🔥 #{rk} hottest today"
            elif rk and rk > of - 3:
                heat = f"🧊 #{rk}/{of} coldest today"
            else:
                heat = f"#{rk}/{of} today" if rk else ""
            # Prefixed "Sector:" so these %% are never mistaken for the stock's own.
            sec_line = (f"\n   Sector {sec['name']}: <b>{sec['day']:+.1f}%</b> today "
                        f"({heat}) · {sec['month']:+.1f}% 1mo")

        lines.append(
            f"<b>{i}. {title}</b> — <b>{r['score']}/100</b> {_band(r['score'])}{star}\n"
            f"   {r['price']:.2f} {r['currency']} · RSI {r['rsi']:.0f}"
            f"{setup_line}{heat_line}{tag_line}{sec_line}"
        )
        lines.append("")   # blank line between setups for readability
    lines.append("<i>Best mix (what actually tested strong): <b>RSI under 30</b> + "
                 "🧱 at tested support · ⚠️ = avoid · 📍 over 55% of range = chasing · "
                 "★ = live trigger.\nPosition size, stop &amp; target: /score SYM</i>")
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
