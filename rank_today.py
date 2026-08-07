"""
King Stocks — live "best-qualified setups right now" ranker
-----------------------------------------------------------
This is NOT a prediction of which stocks will go up. It CANNOT do that, and
neither can anyone. What it DOES: score every watchlist name by how well it
matches the exact edge we backtested (uptrend + pulling back + volume + strong
sector) and show you the best-qualified candidates *at this moment*.

Re-run it any time — it re-analyses live off the current market. High-conviction
setups backtested at +0.84R (real, measured). You still decide and place the trade.

Scoring (max ~7):
  +2  in uptrend  (price above the 50-EMA)
  +2  pullback zone (RSI 30-45 — the dip we buy)
  +1  RSI turning back up (momentum returning)
  +1  volume > 1.3x its 20-bar average (real buying interest)
  +1  its sector is trending up
  -1  RSI > 60 (already extended, the dip has passed)

A name that also meets our strict live-alert trigger is flagged ★ FIRING.

Run:  python rank_today.py
"""

import datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
import scanner as s

TOP_N = 8


def _score_ticker(ticker: str):
    """Return a dict with the current fit score, or None if no data."""
    try:
        # prepost=True so pre-/after-hours bars show up (you trade extended
        # hours on Trade Republic, so this keeps prices as current as possible).
        df = yf.download(ticker, period=s.LOOKBACK, interval=s.INTERVAL,
                         progress=False, auto_adjust=False, prepost=s.EXTENDED_HOURS)
    except Exception:
        return None
    if df is None or len(df) < s.EMA_TREND + 5:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df["EMA"] = s.ema(df["Close"], s.EMA_TREND)
    df["RSI"] = s.rsi(df["Close"], s.RSI_LEN)
    last, prev = df.iloc[-1], df.iloc[-2]
    price   = float(last["Close"])
    ema_val = float(last["EMA"])
    rsi_now = float(last["RSI"])
    rsi_prev = float(prev["RSI"])

    try:
        recent_vol = float(df["Volume"].iloc[-21:-1].mean())
        vol_ratio = float(last["Volume"]) / recent_vol if recent_vol > 0 else 1.0
    except Exception:
        vol_ratio = 1.0

    in_uptrend = price > ema_val
    sec_strong = s.sector_is_strong(ticker)

    score = 0.0
    reasons = []
    if in_uptrend:
        score += 2; reasons.append("uptrend")
    else:
        reasons.append("below trend")
    if 30 <= rsi_now <= 45:
        score += 2; reasons.append("dip zone")
    if rsi_now > rsi_prev:
        score += 1; reasons.append("turning up")
    if vol_ratio > 1.3:
        score += 1; reasons.append(f"vol {vol_ratio:.1f}x")
    if sec_strong:
        score += 1; reasons.append("sector↑")
    if rsi_now > 60:
        score -= 1; reasons.append("extended")

    # Does it also meet the strict live trigger right now?
    firing = bool(in_uptrend and rsi_prev <= s.RSI_OVERSOLD and rsi_now > rsi_prev)

    # Timestamp of the newest candle we used (how fresh this price is).
    try:
        ts = df.index[-1]
        as_of = ts.tz_convert("Europe/Berlin") if ts.tzinfo else ts
    except Exception:
        as_of = None

    return {
        "ticker": ticker,
        "score": score,
        "price": price,
        "currency": s.ticker_currency(ticker),
        "rsi": rsi_now,
        "vol_ratio": vol_ratio,
        "uptrend": in_uptrend,
        "sector_strong": sec_strong,
        "firing": firing,
        "as_of": as_of,
        "reasons": reasons,
    }


def rank(top_n: int = TOP_N):
    """Score the whole watchlist and return the top_n by fit, best first."""
    rows = []
    for t in s.WATCHLIST:
        if t in ("SPY", "QQQ"):   # index ETFs are context, not picks
            continue
        r = _score_ticker(t)
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


def format_ranking(rows, healthy: bool = True) -> str:
    lines = ["👑 <b>King Stocks — best-qualified setups now</b>",
             f"<i>Asked: {_stamp()}</i>"]
    fresh = _freshness_line(rows)
    if fresh:
        lines.append(fresh)
    if not healthy:
        lines.append("\n⚠️ <b>Market regime WEAK</b> (SPY below its 50-day avg). "
                     "Dip-buys are lower-odds now — these are relative rankings only.")
    lines.append("")
    for i, r in enumerate(rows, 1):
        star = " ★FIRING" if r["firing"] else ""
        lines.append(
            f"{i}. <b>{r['ticker']}</b>  score {r['score']:.0f}{star}\n"
            f"   {r['price']:.2f} {r['currency']} · RSI {r['rsi']:.0f} · "
            + " · ".join(r["reasons"])
        )
    lines.append(
        "\n<i>Ranking of CURRENT fit to our validated rules — NOT a prediction. "
        "★FIRING = also meets the strict live trigger. You decide, you place the "
        "trade. Not financial advice.</i>"
    )
    return "\n".join(lines)


def run(send: bool = False, top_n: int = TOP_N):
    healthy = s.market_is_healthy()
    rows = rank(top_n)
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
