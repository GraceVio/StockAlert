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

TOP_N = 10


def _score_100(price, ema_val, rsi_now, rsi_prev, vol_ratio, sec_strong, healthy,
               above_vwap=None, support_dist=None, rel_strength=None):
    """Blend the validated factors into a single 0-100 fit score + reason tags.

    Weights (total 100): Trend 20 · Dip/RSI 25 · Turning up 10 · Volume 10 ·
    VWAP 10 · Support 10 · Rel.strength 10 · Sector 3 · Regime 2.
    The three new factors (VWAP, support, rel.strength) raise the quality bar so
    the top of the list is a dip in a *leader*, near support, that big money backs
    — not just any stock that dipped today."""
    dist = (price / ema_val - 1) * 100 if ema_val else 0.0
    reasons = []

    # Trend (0-20): reward uptrend, fade as it gets over-extended above the EMA.
    if price > ema_val:
        trend = max(8.0, min(20.0, 20.0 - max(0.0, dist - 6.0) * 1.3))
        reasons.append("uptrend")
    else:
        trend = max(0.0, min(8.0, 8.0 + dist))     # dist is negative here
        reasons.append("below trend")

    # Dip / RSI zone (0-25): peaks at RSI ~35, penalises extended AND falling-knife.
    rsi_s = max(0.0, 25.0 - abs(rsi_now - 35.0) * 1.0)
    if 30 <= rsi_now <= 45:
        reasons.append("dip zone")
    elif rsi_now > 60:
        reasons.append("extended")

    # RSI turning back up (0-10).
    if rsi_now > rsi_prev:
        turn = min(10.0, (rsi_now - rsi_prev) * 2.5)
        reasons.append("turning up")
    else:
        turn = 0.0

    # Volume (0-10): above-average buying interest confirms the bounce.
    vol = min(10.0, max(0.0, (vol_ratio - 0.8) * 10.0))
    if vol_ratio >= 1.3:
        reasons.append(f"vol {vol_ratio:.1f}x")

    # VWAP (0-10): is price above the price big investors are averaged into?
    if above_vwap is True:
        vwap_s = 10.0; reasons.append("above VWAP")
    elif above_vwap is False:
        vwap_s = 3.0; reasons.append("below VWAP")
    else:
        vwap_s = 5.0

    # Support (0-10): a dip landing NEAR a support floor is a higher-quality entry.
    if support_dist is None:
        sup = 5.0
    elif support_dist <= 3:
        sup = 10.0; reasons.append("near support")
    elif support_dist <= 6:
        sup = 7.0
    elif support_dist <= 10:
        sup = 4.0
    else:
        sup = 2.0                          # lots of air below = more downside room

    # Relative strength (0-10): leaders (beating the market) get the money flows.
    if rel_strength is None:
        rs = 5.0
    elif rel_strength >= 5:
        rs = 10.0; reasons.append("market leader")
    elif rel_strength >= 0:
        rs = 7.0
    elif rel_strength >= -5:
        rs = 4.0
    else:
        rs = 1.0; reasons.append("lagging market")

    # Sector (0-3) and market regime (0-2).
    if sec_strong is True:
        sec = 3.0; reasons.append("sector↑")
    elif sec_strong is False:
        sec = 0.0
    else:
        sec = 1.5
    reg = 2.0 if healthy else 0.0

    total = trend + rsi_s + turn + vol + vwap_s + sup + rs + sec + reg
    parts = {"Trend": (trend, 20), "Dip/RSI": (rsi_s, 25), "Turning up": (turn, 10),
             "Volume": (vol, 10), "VWAP": (vwap_s, 10), "Support": (sup, 10),
             "Rel.strength": (rs, 10), "Sector": (sec, 3), "Regime": (reg, 2)}

    # Honesty penalties — the two things that break the 'dip in UPTREND' thesis:
    if price <= ema_val:
        total *= 0.80                       # counter-trend bounce = higher risk
        reasons.append("counter-trend risk")
    if rsi_now > 60:
        total -= (rsi_now - 60) * 1.8       # extended: the dip already passed

    return int(round(max(0.0, min(100.0, total)))), reasons, parts


def _score_from_df(ticker, df, healthy, ctx=None):
    """Compute the fit row for one ticker from its already-downloaded frame.
    `ctx` is the daily VWAP/support/rel-strength bundle (scanner.daily_context)."""
    if df is None or len(df) < s.EMA_TREND + 5:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    if "Close" not in df or df["Close"].dropna().empty:
        return None

    df = df.dropna(subset=["Close"])
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
        above_vwap=ctx.get("above_vwap"), support_dist=ctx.get("support_dist"),
        rel_strength=ctx.get("rel_strength"))
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
        "as_of": as_of, "reasons": reasons, "parts": parts,
        "stop": stop, "stop_pct": stop_pct, "risk": risk,
        "vwap": ctx.get("vwap"), "above_vwap": ctx.get("above_vwap"),
        "support": ctx.get("support"), "support_label": ctx.get("support_label"),
        "support_dist": ctx.get("support_dist"), "rel_strength": ctx.get("rel_strength"),
    }


def _support_line(r) -> str:
    """Plain-language support + VWAP + leader status line."""
    cur = r["currency"]
    bits = []
    lvl, lab, dist = r.get("support"), r.get("support_label"), r.get("support_dist")
    if lvl is not None and dist is not None:
        near = "🟢 right at it" if dist <= 3 else (
               "🟡 a bit above" if dist <= 6 else "⚪ well above")
        bits.append(f"🧱 Support {lvl:.2f} {cur} ({dist:.1f}% below) — {near} · {lab}")
    av = r.get("above_vwap")
    if av is True:
        bits.append("💧 Above VWAP — big investors are supporting this price")
    elif av is False:
        bits.append("💧 Below VWAP — big investors are averaged in higher (caution)")
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
    target = r["price"] + s.RR_TARGET * (r["price"] - stop)
    tgt_pct = (target - r["price"]) / r["price"] * 100
    out = (f"\n🛑 Stop {stop:.2f} {cur} ({stop_pct:+.1f}%) · "
           f"🎯 Target {target:.2f} {cur} ({tgt_pct:+.1f}%, {s.RR_TARGET:.0f}R)")
    if rp:
        cap = " (capped — no leverage)" if rp.get("capped") else ""
        out += (f"\n💰 Buy ≈ <b>€{rp['pos_val']:.0f}</b> (~{rp['shares']:.1f} shares){cap}\n"
                f"   → if the stop hits you lose only <b>€{rp['risk_eur']:.0f}</b> "
                f"({rp['risk_pct']:.0f}% of your €{rp['account']:.0f})")
    return out


def score_one(ticker: str, healthy=None) -> str:
    """Detailed 0-100 breakdown for a single ticker (any symbol you type)."""
    ticker = ticker.strip().upper()
    if not ticker:
        return "Usage: /score SYM   (e.g. /score NVDA or /score SAP.DE)"
    if healthy is None:
        healthy = s.market_is_healthy()
    try:
        df = yf.download(ticker, period=s.LOOKBACK, interval=s.INTERVAL,
                         progress=False, auto_adjust=False, prepost=s.EXTENDED_HOURS)
    except Exception:
        df = None
    # daily frame for VWAP / support / relative strength
    r0 = _score_from_df(ticker, df, healthy)
    ctx = None
    if r0 is not None:
        try:
            dd = yf.download(ticker, period="1y", interval="1d",
                             progress=False, auto_adjust=False)
            ctx = s.daily_context(ticker, dd, r0["price"])
        except Exception:
            ctx = None
    r = _score_from_df(ticker, df, healthy, ctx=ctx) if ctx else r0
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
        nemoji, nword = nw.stock_lean(ticker)
        news_line = f"\n📰 News lean: {nemoji} <b>{nword}</b>  <i>(rough headline read)</i>" if nemoji else ""
    except Exception:
        news_line = ""

    return (
        f"🔎 <b>{title}</b> — <b>{r['score']}/100</b> {_band(r['score'])}{star}\n"
        f"{fresh}\n\n"
        f"Price <b>{r['price']:.2f} {cur}</b> · RSI {r['rsi']:.0f} · "
        f"{'uptrend' if r['uptrend'] else 'below trend'} · vol {r['vol_ratio']:.1f}×"
        f"{_support_line(r)}"
        f"{_size_line(r)}"
        f"{earn}{news_line}\n\n"
        f"<b>Score breakdown</b>\n<pre>{breakdown}</pre>\n"
        f"{(' · '.join(r['reasons']))}\n\n"
        f"<i>0-100 = how well this fits the dip-in-uptrend edge right now (not a "
        f"price prediction). Higher = better entry quality.</i>"
    )


def rank(top_n: int = TOP_N, healthy=None):
    """Batch-scan the wide universe and return the top_n by 0-100 fit score."""
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
    # …and a daily frame for VWAP / support / relative strength (longer-term).
    try:
        ddata = yf.download(universe, period="1y", interval="1d",
                            progress=False, auto_adjust=False,
                            group_by="ticker", threads=True)
    except Exception:
        ddata = None

    rows = []
    for t in universe:
        try:
            df = data[t] if data is not None else None
        except Exception:
            df = None
        r0 = _score_from_df(t, df, healthy)  # need price for context; cheap re-first pass
        price = r0["price"] if r0 else None
        ctx = None
        if price is not None and ddata is not None:
            try:
                ctx = s.daily_context(t, ddata[t], price)
            except Exception:
                ctx = None
        r = _score_from_df(t, df, healthy, ctx=ctx) if ctx else r0
        if r:
            rows.append(r)
    rows.sort(key=lambda x: (x["score"], x["vol_ratio"]), reverse=True)
    return rows[:top_n]


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
    age_h = (dt.datetime.now(ZoneInfo("Europe/Berlin")) - newest).total_seconds() / 3600
    when = newest.strftime("%d %b %H:%M CET")
    phase = _us_session()
    label = {
        "regular": "US market OPEN · live (~15-min delayed)",
        "pre": "US pre-market · live extended-hours prices (thin volume)",
        "post": "US after-hours · live extended-hours prices (thin volume)",
        "closed": f"US market CLOSED — latest available price (~{age_h:.0f}h old)",
    }[phase]
    return f"📈 Data as of <b>{when}</b> · {label}"


def _band(score: int) -> str:
    if score >= 75:
        return "🟢 strong fit"
    if score >= 60:
        return "🟢 good fit"
    if score >= 45:
        return "🟡 watch"
    return "⚪ weak fit"


def format_ranking(rows, healthy: bool = True) -> str:
    lines = ["👑 <b>King Stocks — best-qualified setups now</b>",
             f"<i>Asked: {_stamp()} · scanned {len(s.rank_universe())} stocks</i>"]
    fresh = _freshness_line(rows)
    if fresh:
        lines.append(fresh)
    if not healthy:
        lines.append("\n⚠️ <b>Market regime WEAK</b> (SPY below its 50-day avg). "
                     "Dip-buys are lower-odds now — these are relative rankings only.")
    lines.append("")
    for i, r in enumerate(rows, 1):
        star = " ★FIRING" if r["firing"] else ""
        nm = s.name_for(r["ticker"])
        title = f"{r['ticker']}" + (f" · {nm}" if nm else "")
        # compact quality tags
        tags = []
        if r.get("support_dist") is not None and r["support_dist"] <= 3:
            tags.append("🧱near support")
        if r.get("above_vwap") is True:
            tags.append("💧above VWAP")
        if r.get("rel_strength") is not None and r["rel_strength"] >= 5:
            tags.append("🏆leader")
        tag_line = ("  " + " · ".join(tags)) if tags else ""
        # compact size line
        rp = r.get("risk")
        size_line = ""
        if rp:
            size_line = (f"\n   💰 ≈€{rp['pos_val']:.0f} · risk €{rp['risk_eur']:.0f} "
                         f"if stop {r['stop_pct']:+.1f}%")
        lines.append(
            f"{i}. <b>{title}</b> — <b>{r['score']}/100</b> {_band(r['score'])}{star}\n"
            f"   {r['price']:.2f} {r['currency']} · RSI {r['rsi']:.0f}{tag_line}"
            f"{size_line}"
        )
    lines.append(
        "\n<i>0-100 = entry-quality fit to the dip-in-uptrend edge right now (not a "
        "price prediction). 🧱near support · 💧above VWAP (big money supports it) · "
        "🏆leader (beating the market). ★FIRING = also meets the strict live trigger. "
        "💰 sizes so a stop-out risks ~{:.0f}% of your account — set it with /account.</i>"
        .format(s.RISK_PCT)
    )
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
