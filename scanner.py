"""
Stock Alert Bot - v1
--------------------
Scans a watchlist of US + German/EU stocks on the 15-minute chart and sends a
Telegram alert when a "buy-the-dip in an uptrend" setup appears.

Strategy (long only):
  1. Trend filter : price must be ABOVE the 50-period EMA (only dips in uptrends).
  2. Pullback     : RSI(14) dipped into oversold (<= RSI_OVERSOLD) on the
                    previous candle and has TURNED BACK UP on the latest candle.
  3. Risk levels  : every alert carries a stop (1.5 x ATR below entry) and a
                    target (2x the risk).

It does NOT predict. It flags a defined setup with a capped, known risk.
You decide and place the trade yourself.

Runs once per invocation (designed to be called every ~15 min by a scheduler).
Because the trigger is a *cross event* (RSI turning up on the newest candle),
it naturally avoids spamming the same setup repeatedly.
"""

import os
import sys
import requests
import pandas as pd
import yfinance as yf

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

# Your watchlist. US tickers are plain; German/EU use the exchange suffix
# (.DE = Xetra). Add or remove freely.
WATCHLIST = [
    # --- US tech (your names) ---
    "NVDA", "MRVL", "TEAM", "NOW", "CRWV", "INTC", "INTU", "IREN",
    "SHOP", "ZS", "DELL", "IONQ", "UPST", "ARM", "PODD", "AAPL",
    # --- German / EU (daytime hours from Germany) ---
    "SAP.DE",    # SAP
    "SIE.DE",    # Siemens
    "ASML.AS",   # ASML (Amsterdam)
    "AIR.DE",    # Airbus
    "ALV.DE",    # Allianz
]

# Strategy parameters
INTERVAL       = "15m"   # candle size
LOOKBACK       = "5d"    # how much history to pull (15m data is limited to ~60d)
EMA_TREND      = 50      # trend filter length
RSI_LEN        = 14
RSI_OVERSOLD   = 35      # pullback threshold
ATR_LEN        = 14
ATR_STOP_MULT  = 1.5     # stop = entry - 1.5 * ATR
RR_TARGET      = 2.0     # target = entry + 2.0 * risk

# Telegram credentials come from environment variables (set as secrets in the
# cloud runner). Never hard-code these.
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Tickers quoting in these currencies are already EUR (no conversion needed).
# Everything else on the watchlist is assumed USD (US stocks).
EUR_SUFFIXES = (".DE", ".AS", ".PA", ".MI", ".MC", ".F", ".BR", ".VI", ".LS", ".HE")


def ticker_currency(ticker: str) -> str:
    return "EUR" if ticker.upper().endswith(EUR_SUFFIXES) else "USD"


# EUR/USD rate is fetched once per run and reused for all USD tickers.
_eurusd_cache = {"rate": None}


def get_eurusd() -> float:
    """Live EUR/USD rate (how many USD per 1 EUR). Returns None if unavailable."""
    if _eurusd_cache["rate"] is not None:
        return _eurusd_cache["rate"]
    try:
        fx = yf.download("EURUSD=X", period="1d", interval="15m",
                         progress=False, auto_adjust=False)
        if fx is not None and len(fx):
            if isinstance(fx.columns, pd.MultiIndex):
                fx.columns = fx.columns.get_level_values(0)
            _eurusd_cache["rate"] = float(fx["Close"].iloc[-1])
    except Exception as e:
        print(f"  EUR/USD fetch failed: {e}")
    return _eurusd_cache["rate"]


# ----------------------------------------------------------------------------
# INDICATORS (pure pandas, no heavy TA libraries to install)
# ----------------------------------------------------------------------------

def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, length: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


# ----------------------------------------------------------------------------
# SIGNAL LOGIC
# ----------------------------------------------------------------------------

def check_ticker(ticker: str):
    """Return an alert dict if a setup fires on the latest candle, else None."""
    try:
        df = yf.download(
            ticker, period=LOOKBACK, interval=INTERVAL,
            progress=False, auto_adjust=False,
        )
    except Exception as e:
        print(f"  {ticker}: download error: {e}")
        return None

    if df is None or len(df) < EMA_TREND + 5:
        print(f"  {ticker}: not enough data")
        return None

    # yfinance can return multi-index columns for a single ticker; flatten.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df["EMA"] = ema(df["Close"], EMA_TREND)
    df["RSI"] = rsi(df["Close"], RSI_LEN)
    df["ATR"] = atr(df, ATR_LEN)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    price   = float(last["Close"])
    ema_val = float(last["EMA"])
    rsi_now = float(last["RSI"])
    rsi_prev = float(prev["RSI"])
    atr_val = float(last["ATR"])

    in_uptrend    = price > ema_val
    was_oversold  = rsi_prev <= RSI_OVERSOLD
    turning_up    = rsi_now > rsi_prev

    if in_uptrend and was_oversold and turning_up:
        stop   = price - ATR_STOP_MULT * atr_val
        risk   = price - stop
        target = price + RR_TARGET * risk
        return {
            "ticker": ticker,
            "currency": ticker_currency(ticker),
            "price": price,
            "stop": stop,
            "target": target,
            "rsi_prev": rsi_prev,
            "rsi_now": rsi_now,
            # exact percentage moves — currency- and spread-independent,
            # so they apply directly on the Trade Republic (EUR) screen.
            "stop_pct": (stop - price) / price * 100,
            "target_pct": (target - price) / price * 100,
        }
    return None


# ----------------------------------------------------------------------------
# TELEGRAM
# ----------------------------------------------------------------------------

def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("!! Telegram not configured (missing TELEGRAM_TOKEN / CHAT_ID).")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=20)
        if r.status_code != 200:
            print(f"!! Telegram error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"!! Telegram send failed: {e}")


def _eur_line(a: dict) -> str:
    """Approximate EUR conversion line for USD-quoted stocks."""
    if a["currency"] == "EUR":
        return ""  # already in EUR, matches Trade Republic
    rate = get_eurusd()
    if not rate:
        return "\n<i>(EUR rate unavailable — use the % levels below)</i>"
    e_entry  = a["price"] / rate
    e_stop   = a["stop"] / rate
    e_target = a["target"] / rate
    return (
        f"\n≈ EUR  entry {e_entry:.2f} · stop {e_stop:.2f} · target {e_target:.2f}"
        f"  <i>(approx, TR adds a small spread)</i>"
    )


def format_alert(a: dict) -> str:
    cur = a["currency"]
    return (
        f"🔔 <b>{a['ticker']}</b> long setup ({INTERVAL})\n"
        f"Buy-the-dip in uptrend · RSI turned up from {a['rsi_prev']:.0f} → {a['rsi_now']:.0f}\n\n"
        f"Entry ~ <b>{a['price']:.2f} {cur}</b>\n"
        f"Stop   {a['stop']:.2f} {cur}  ({a['stop_pct']:+.1f}%)\n"
        f"Target {a['target']:.2f} {cur}  ({a['target_pct']:+.1f}%)"
        f"{_eur_line(a)}\n\n"
        f"<b>Apply in Trade Republic using the % levels</b> — "
        f"stop {a['stop_pct']:+.1f}%, target {a['target_pct']:+.1f}% from your fill.\n"
        f"<i>You decide and place the trade. Not financial advice.</i>"
    )


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    print(f"Scanning {len(WATCHLIST)} tickers on {INTERVAL}...")
    alerts = []
    for ticker in WATCHLIST:
        a = check_ticker(ticker)
        if a:
            print(f"  ** SETUP: {ticker} @ {a['price']:.2f}")
            alerts.append(a)
        else:
            print(f"  {ticker}: no setup")

    for a in alerts:
        send_telegram(format_alert(a))

    print(f"Done. {len(alerts)} alert(s) sent.")


if __name__ == "__main__":
    main()
