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

TOP_N = 10


def _score_100(price, ema_val, rsi_now, rsi_prev, vol_ratio, sec_strong, healthy):
    """Blend the validated factors into a single 0-100 fit score + reason tags."""
    dist = (price / ema_val - 1) * 100 if ema_val else 0.0
    reasons = []

    # Trend (0-25): reward uptrend, fade as it gets over-extended above the EMA.
    if price > ema_val:
        trend = max(10.0, min(25.0, 25.0 - max(0.0, dist - 6.0) * 1.5))
        reasons.append("uptrend")
    else:
        trend = max(0.0, min(10.0, 10.0 + dist))   # dist is negative here
        reasons.append("below trend")

    # Dip / RSI zone (0-30): peaks at RSI ~35, penalises extended AND falling-knife.
    rsi_s = max(0.0, 30.0 - abs(rsi_now - 35.0) * 1.2)
    if 30 <= rsi_now <= 45:
        reasons.append("dip zone")
    elif rsi_now > 60:
        reasons.append("extended")

    # RSI turning back up (0-15).
    if rsi_now > rsi_prev:
        turn = min(15.0, (rsi_now - rsi_prev) * 3.0)
        reasons.append("turning up")
    else:
        turn = 0.0

    # Volume (0-15): above-average buying interest.
    vol = min(15.0, max(0.0, (vol_ratio - 0.8) * 15.0))
    if vol_ratio >= 1.3:
        reasons.append(f"vol {vol_ratio:.1f}x")

    # Sector (0-10) and market regime (0-5).
    if sec_strong is True:
        sec = 10.0; reasons.append("sector↑")
    elif sec_strong is False:
        sec = 0.0
    else:
        sec = 5.0
    reg = 5.0 if healthy else 0.0

    total = trend + rsi_s + turn + vol + sec + reg
    parts = {"Trend": (trend, 25), "Dip/RSI": (rsi_s, 30), "Turning up": (turn, 15),
             "Volume": (vol, 15), "Sector": (sec, 10), "Regime": (reg, 5)}

    # Honesty penalties — the two things that break the 'dip in UPTREND' thesis:
    if price <= ema_val:
        total *= 0.80                       # counter-trend bounce = higher risk
        reasons.append("counter-trend risk")
    if rsi_now > 60:
        total -= (rsi_now - 60) * 1.8       # extended: the dip already passed

    return int(round(max(0.0, min(100.0, total)))), reasons, parts


def _score_from_df(ticker, df, healthy):
    """Compute the fit row for one ticker from its already-downloaded frame."""
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

    sec_strong = s.sector_is_strong(ticker)
    score, reasons, parts = _score_100(price, ema_val, rsi_now, rsi_prev,
                                       vol_ratio, sec_strong, healthy)
    firing = bool(price > ema_val and rsi_prev <= s.RSI_OVERSOLD and rsi_now > rsi_prev)

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
    }


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
    r = _score_from_df(ticker, df, healthy)
    if not r:
        return (f"❌ No usable data for <b>{ticker}</b>. Check the symbol "
                f"(EU names need a suffix like .DE .AS .PA .L .F).")

    nm = s.name_for(ticker)
    title = ticker + (f" · {nm}" if nm else "")
    cur = r["currency"]

    # ATR-based reference stop/target (same rules the alerts use).
    stop_line = ""
    try:
        d2 = df.copy()
        if isinstance(d2.columns, pd.MultiIndex):
            d2.columns = d2.columns.get_level_values(0)
        atr_val = float(s.atr(d2, s.ATR_LEN).iloc[-1])
        stop = r["price"] - s.ATR_STOP_MULT * atr_val
        target = r["price"] + s.RR_TARGET * (r["price"] - stop)
        stop_pct = (stop - r["price"]) / r["price"] * 100
        tgt_pct = (target - r["price"]) / r["price"] * 100
        stop_line = (f"\n🛑 Ref. stop {stop:.2f} {cur} ({stop_pct:+.1f}%) · "
                     f"🎯 target {target:.2f} {cur} ({tgt_pct:+.1f}%, {s.RR_TARGET:.0f}R)")
    except Exception:
        pass

    breakdown = "\n".join(
        f"   {k:<11} {v:>4.0f}/{mx}" for k, (v, mx) in r["parts"].items()
    )
    star = " ★FIRING" if r["firing"] else ""
    fresh = _freshness_line([r])
    return (
        f"🔎 <b>{title}</b> — <b>{r['score']}/100</b> {_band(r['score'])}{star}\n"
        f"{fresh}\n\n"
        f"Price <b>{r['price']:.2f} {cur}</b> · RSI {r['rsi']:.0f} · "
        f"{'uptrend' if r['uptrend'] else 'below trend'} · vol {r['vol_ratio']:.1f}×"
        f"{stop_line}\n\n"
        f"<b>Score breakdown</b>\n<pre>{breakdown}</pre>\n"
        f"{(' · '.join(r['reasons']))}\n\n"
        f"<i>0-100 = fit to our validated dip-in-uptrend edge NOW — not a price "
        f"prediction. You decide. Not financial advice.</i>"
    )


def rank(top_n: int = TOP_N, healthy=None):
    """Batch-scan the wide universe and return the top_n by 0-100 fit score."""
    if healthy is None:
        healthy = s.market_is_healthy()
    universe = s.rank_universe()
    try:
        data = yf.download(universe, period=s.LOOKBACK, interval=s.INTERVAL,
                           progress=False, auto_adjust=False,
                           prepost=s.EXTENDED_HOURS, group_by="ticker", threads=True)
    except Exception:
        data = None

    rows = []
    for t in universe:
        try:
            df = data[t] if data is not None else None
        except Exception:
            df = None
        r = _score_from_df(t, df, healthy)
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
        lines.append(
            f"{i}. <b>{title}</b> — <b>{r['score']}/100</b> {_band(r['score'])}{star}\n"
            f"   {r['price']:.2f} {r['currency']} · RSI {r['rsi']:.0f} · "
            + " · ".join(r["reasons"])
        )
    lines.append(
        "\n<i>0-100 = fit to our validated dip-in-uptrend edge RIGHT NOW — NOT a "
        "prediction of price. ★FIRING = also meets the strict live trigger. "
        "You decide and place the trade. Not financial advice.</i>"
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
