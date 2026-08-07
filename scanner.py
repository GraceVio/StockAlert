"""
Stock Alert Bot - v2
--------------------
Scans a watchlist of US + EU/UK stocks on the 15-minute chart and sends a
Telegram alert when a "buy-the-dip in an uptrend" setup appears.

Strategy (long only):
  1. Market regime : the broad market (SPY) must be healthy (above its 50-day
                     average). We do not buy dips while the whole market falls.
  2. Trend filter  : the stock must be ABOVE its 50-period EMA (uptrend only).
  3. Pullback      : RSI(14) dipped into oversold (<= RSI_OVERSOLD) on the
                     previous candle and has TURNED BACK UP on the latest candle.
  4. Risk levels   : every alert carries a stop (1.5 x ATR below entry) and a
                     target (2x the risk).
  5. Earnings guard: if the stock reports earnings within EARNINGS_WARN_DAYS,
                     the alert is TAGGED with a warning (gap risk).

It does NOT predict. It flags a defined setup with a capped, known risk.
You decide and place the trade yourself. Not financial advice.

Every alert is also appended to alerts_log.csv so performance can be reviewed.
"""

import os
import csv
import datetime as dt
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import yfinance as yf

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

# Your watchlist. US tickers are plain; EU/UK use the Yahoo Finance exchange
# suffix (.DE Xetra, .AS Amsterdam, .PA Paris, .L London, .F Frankfurt).
WATCHLIST = [
    # --- Index ETFs (clean mean-reverters — ideal for dip-buying) ---
    "SPY", "QQQ",
    # --- US mega / large-cap tech ---
    "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "AVGO", "TSM",
    "AMD", "QCOM", "MU", "ORCL", "CRM", "ADBE", "NOW", "INTU",
    "NFLX", "TSLA", "PLTR", "IBM", "DELL", "ARM", "MRVL", "INTC",
    # --- US large-cap non-tech ---
    "LLY", "NVO", "KO", "PEP", "COST", "WMT", "MCD", "V", "MA",
    "JPM", "UNH", "HD", "XOM", "CVX", "BSX", "JD", "FICO",
    # --- Europe large-cap (EUR) ---
    "SAP.DE", "SIE.DE", "ASML.AS", "AIR.DE", "ALV.DE", "DTE.DE",
    "IFX.DE", "SHELL.AS", "MC.PA", "OR.PA", "BY6.F",
]
# Trimmed illiquid / hyper-speculative names are listed in TRIMMED_STOCKS.md
# (kept out on purpose — see that file for the reasons).

# Strategy parameters
INTERVAL          = "15m"   # candle size
LOOKBACK          = "5d"    # history to pull (15m data limited to ~60d)
EMA_TREND         = 50      # trend filter length
RSI_LEN           = 14
RSI_OVERSOLD      = 35      # pullback threshold
ATR_LEN           = 14
ATR_STOP_MULT     = 1.5     # stop = entry - 1.5 * ATR
RR_TARGET         = 3.0     # target = entry + 3.0 * risk (optimized 2026-08-07)

# Upgrades
MARKET_REGIME_FILTER = True   # only fire longs when SPY is above its 50-day avg
MARKET_INDEX         = "SPY"  # broad-market proxy
EARNINGS_WARN_DAYS   = 7      # warn if earnings within this many days
LOG_FILE             = "alerts_log.csv"

# --- Position sizing ---
# "smart" = grade each setup (volume/trend/sector) → suggest size + adaptive target
# "fixed" = always invest FIXED_EUR and take profit at PROFIT_TARGET_EUR
# "risk"  = size each trade so a stop-out loses RISK_PCT of ACCOUNT_EUR
POSITION_MODE     = "smart"
FIXED_EUR         = 3000.0   # amount per trade (fixed mode)
PROFIT_TARGET_EUR = 100.0    # euro profit to take (fixed mode)
ACCOUNT_EUR       = 3000.0   # account size (risk mode + safety cap)
RISK_PCT          = 1.0      # % risked per trade (risk mode)
ALLOW_LEVERAGE    = False

# Smart mode: euro size per conviction grade (EDIT to your comfort)
CONVICTION_EUR    = {"Low": 1500.0, "Medium": 3000.0, "High": 5000.0}
# Adaptive reward:risk multiple per grade (bigger target when the setup is better)
CONVICTION_RR     = {"Low": 2.0, "Medium": 2.5, "High": 3.0}
SHOW_HEADLINES    = True      # surface recent headlines for context (best effort)

# --- US opening-volatility guard ---
OPENING_GUARD     = True     # skip NEW US alerts during the wild first minutes of the open
OPENING_GUARD_MIN = 30       # how many minutes after the US open (09:30 ET) to skip

# Telegram credentials come from environment variables (secrets in the runner).
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Tickers with these suffixes already quote in EUR (no conversion needed).
EUR_SUFFIXES = (".DE", ".AS", ".PA", ".MI", ".MC", ".F", ".BR", ".VI", ".LS", ".HE")
GBP_SUFFIXES = (".L",)


def ticker_currency(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(EUR_SUFFIXES):
        return "EUR"
    if t.endswith(GBP_SUFFIXES):
        return "GBP"
    return "USD"


# FX rates fetched once per run and reused (USD per 1 EUR, GBP per 1 EUR).
_fx_cache = {"EURUSD=X": None, "EURGBP=X": None}


def _get_fx(pair: str) -> float:
    if _fx_cache.get(pair) is not None:
        return _fx_cache[pair]
    try:
        fx = yf.download(pair, period="1d", interval="15m",
                         progress=False, auto_adjust=False)
        if fx is not None and len(fx):
            if isinstance(fx.columns, pd.MultiIndex):
                fx.columns = fx.columns.get_level_values(0)
            _fx_cache[pair] = float(fx["Close"].iloc[-1])
    except Exception as e:
        print(f"  {pair} fetch failed: {e}")
    return _fx_cache.get(pair)


def get_eurusd() -> float:
    """Live EUR/USD rate (USD per 1 EUR). Returns None if unavailable."""
    return _get_fx("EURUSD=X")


def to_eur(price: float, currency: str):
    """Convert a price in the stock's currency to EUR. None if rate missing."""
    if currency == "EUR":
        return price
    if currency == "USD":
        r = _get_fx("EURUSD=X")
        return price / r if r else None
    if currency == "GBP":
        r = _get_fx("EURGBP=X")
        return price / r if r else None
    return None


def position_size(a: dict):
    """Position plan. In 'fixed' mode: invest FIXED_EUR, take PROFIT_TARGET_EUR.
    In 'risk' mode: size so a stop-out loses ~RISK_PCT of ACCOUNT_EUR."""
    entry_eur = to_eur(a["price"], a["currency"])
    if not entry_eur or entry_eur <= 0:
        return None
    per_share_risk = entry_eur * abs(a["stop_pct"]) / 100.0

    if POSITION_MODE == "smart":
        invest = CONVICTION_EUR.get(a.get("grade", "Medium"), 3000.0)
        shares = invest / entry_eur
        return {
            "mode": "smart",
            "shares": shares,
            "pos_val": invest,
            "risk_eur": shares * per_share_risk,  # loss if safety stop hit
            "entry_eur": entry_eur,
        }

    if POSITION_MODE == "fixed":
        if FIXED_EUR <= 0:
            return None
        shares = FIXED_EUR / entry_eur
        take_gain_pct = PROFIT_TARGET_EUR / FIXED_EUR * 100.0     # e.g. 3.33%
        take_native = a["price"] * (1 + PROFIT_TARGET_EUR / FIXED_EUR)
        return {
            "mode": "fixed",
            "shares": shares,
            "pos_val": FIXED_EUR,
            "take_native": take_native,          # sell price for +PROFIT_TARGET_EUR
            "take_gain_pct": take_gain_pct,
            "risk_eur": shares * per_share_risk,  # loss IF the safety stop is hit
            "entry_eur": entry_eur,
        }

    # risk mode
    if ACCOUNT_EUR <= 0 or RISK_PCT <= 0 or per_share_risk <= 0:
        return None
    budget = ACCOUNT_EUR * RISK_PCT / 100.0
    shares = budget / per_share_risk
    pos_val = shares * entry_eur
    capped = False
    if not ALLOW_LEVERAGE and pos_val > ACCOUNT_EUR:
        shares = ACCOUNT_EUR / entry_eur
        pos_val = shares * entry_eur
        capped = True
    return {
        "mode": "risk",
        "shares": shares,
        "pos_val": pos_val,
        "risk_eur": shares * per_share_risk,
        "entry_eur": entry_eur,
        "capped": capped,
    }


def in_us_opening() -> bool:
    """True during the first OPENING_GUARD_MIN minutes after the US open (09:30 ET)."""
    try:
        ny = dt.datetime.now(ZoneInfo("America/New_York"))
        if ny.weekday() >= 5:
            return False
        minutes = ny.hour * 60 + ny.minute
        open_min = 9 * 60 + 30
        return open_min <= minutes < open_min + OPENING_GUARD_MIN
    except Exception:
        return False


# ----------------------------------------------------------------------------
# INDICATORS (pure pandas, no heavy TA libraries)
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
# MARKET REGIME  (answers: "is the whole market healthy right now?")
# ----------------------------------------------------------------------------

def market_is_healthy() -> bool:
    """True if the broad market (SPY) is above its 50-day average."""
    if not MARKET_REGIME_FILTER:
        return True
    try:
        m = yf.download(MARKET_INDEX, period="6mo", interval="1d",
                        progress=False, auto_adjust=False)
        if m is None or len(m) < 55:
            print("  regime: not enough index data -> allowing trades")
            return True
        if isinstance(m.columns, pd.MultiIndex):
            m.columns = m.columns.get_level_values(0)
        sma50 = m["Close"].rolling(50).mean().iloc[-1]
        last = float(m["Close"].iloc[-1])
        healthy = last > float(sma50)
        print(f"  regime: {MARKET_INDEX} {last:.2f} vs 50d {float(sma50):.2f} "
              f"-> {'HEALTHY' if healthy else 'WEAK (longs suppressed)'}")
        return healthy
    except Exception as e:
        print(f"  regime check failed: {e} -> allowing trades")
        return True


# ----------------------------------------------------------------------------
# SECTOR STRENGTH  (used as a conviction factor)
# ----------------------------------------------------------------------------

SECTOR_ETFS = {
    "Technology": "XLK", "Comm. Services": "XLC", "Consumer Disc.": "XLY",
    "Consumer Staples": "XLP", "Energy": "XLE", "Financials": "XLF",
    "Health Care": "XLV", "Industrials": "XLI", "Materials": "XLB",
    "Real Estate": "XLRE", "Utilities": "XLU",
}

# Which sector each watchlist ticker belongs to (US sector ETFs used as a
# global proxy — EU names too, since sector trends are largely global).
SECTOR_MAP = {
    "QQQ": "Technology", "NVDA": "Technology", "AAPL": "Technology",
    "MSFT": "Technology", "AVGO": "Technology", "TSM": "Technology",
    "AMD": "Technology", "QCOM": "Technology", "MU": "Technology",
    "ORCL": "Technology", "CRM": "Technology", "ADBE": "Technology",
    "NOW": "Technology", "INTU": "Technology", "PLTR": "Technology",
    "IBM": "Technology", "DELL": "Technology", "ARM": "Technology",
    "MRVL": "Technology", "INTC": "Technology", "FICO": "Technology",
    "SAP.DE": "Technology", "ASML.AS": "Technology", "IFX.DE": "Technology",
    "GOOGL": "Comm. Services", "META": "Comm. Services", "NFLX": "Comm. Services",
    "DTE.DE": "Comm. Services",
    "AMZN": "Consumer Disc.", "TSLA": "Consumer Disc.", "HD": "Consumer Disc.",
    "MCD": "Consumer Disc.", "JD": "Consumer Disc.", "MC.PA": "Consumer Disc.",
    "BY6.F": "Consumer Disc.",
    "KO": "Consumer Staples", "PEP": "Consumer Staples", "COST": "Consumer Staples",
    "WMT": "Consumer Staples", "OR.PA": "Consumer Staples",
    "LLY": "Health Care", "NVO": "Health Care", "UNH": "Health Care",
    "BSX": "Health Care",
    "V": "Financials", "MA": "Financials", "JPM": "Financials", "ALV.DE": "Financials",
    "XOM": "Energy", "CVX": "Energy", "SHELL.AS": "Energy",
    "SIE.DE": "Industrials", "AIR.DE": "Industrials",
}

_sector_cache = {"data": None}


def get_sector_strength():
    """Return {sector: {'month': %, 'strong': bool}} once per run."""
    if _sector_cache["data"] is not None:
        return _sector_cache["data"]
    out = {}
    for name, etf in SECTOR_ETFS.items():
        try:
            df = yf.download(etf, period="4mo", interval="1d",
                             progress=False, auto_adjust=False)
            if df is None or len(df) < 55:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            close = df["Close"]
            last = float(close.iloc[-1])
            month_ago = float(close.iloc[-21])
            sma50 = float(close.rolling(50).mean().iloc[-1])
            out[name] = {"month": (last - month_ago) / month_ago * 100,
                         "strong": last > sma50}
        except Exception:
            continue
    _sector_cache["data"] = out
    return out


def sector_is_strong(ticker: str):
    """True/False if the ticker's sector is trending up; None if unknown."""
    sec = SECTOR_MAP.get(ticker)
    if not sec:
        return None
    s = get_sector_strength().get(sec)
    if not s:
        return None
    return s["strong"] and s["month"] > 0


# ----------------------------------------------------------------------------
# HEADLINES  (context only — surfaced, never used to predict direction)
# ----------------------------------------------------------------------------

def get_headlines(ticker: str, limit: int = 2):
    if not SHOW_HEADLINES:
        return []
    try:
        news = yf.Ticker(ticker).news or []
        titles = []
        for n in news[:limit]:
            t = (n.get("content", {}) or {}).get("title") or n.get("title")
            if t:
                titles.append(t)
        return titles
    except Exception:
        return []


# ----------------------------------------------------------------------------
# EARNINGS GUARD  (gap risk around reports)
# ----------------------------------------------------------------------------

def days_to_earnings(ticker: str):
    """Days until the next earnings date, or None if unknown."""
    try:
        cal = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if cal is None or cal.empty:
            return None
        now = pd.Timestamp.now(tz=cal.index.tz)
        future = cal.index[cal.index > now]
        if len(future) == 0:
            return None
        return int((future.min() - now).days)
    except Exception:
        return None


# ----------------------------------------------------------------------------
# SIGNAL LOGIC
# ----------------------------------------------------------------------------

def check_ticker(ticker: str):
    """Return an alert dict if a setup fires on the latest candle, else None."""
    try:
        df = yf.download(ticker, period=LOOKBACK, interval=INTERVAL,
                         progress=False, auto_adjust=False)
    except Exception as e:
        print(f"  {ticker}: download error: {e}")
        return None

    if df is None or len(df) < EMA_TREND + 5:
        print(f"  {ticker}: not enough data")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df["EMA"] = ema(df["Close"], EMA_TREND)
    df["RSI"] = rsi(df["Close"], RSI_LEN)
    df["ATR"] = atr(df, ATR_LEN)

    last, prev = df.iloc[-1], df.iloc[-2]
    price    = float(last["Close"])
    ema_val  = float(last["EMA"])
    rsi_now  = float(last["RSI"])
    rsi_prev = float(prev["RSI"])
    atr_val  = float(last["ATR"])

    in_uptrend   = price > ema_val
    was_oversold = rsi_prev <= RSI_OVERSOLD
    turning_up   = rsi_now > rsi_prev

    if in_uptrend and was_oversold and turning_up:
        # Skip new US entries during the wild first minutes of the US open.
        if OPENING_GUARD and ticker_currency(ticker) == "USD" and in_us_opening():
            print(f"  {ticker}: setup but skipped (US opening guard)")
            return None

        # --- Conviction factors (measurable, not predictive) ---
        try:
            recent_vol = float(df["Volume"].iloc[-21:-1].mean())
            vol_ratio = float(last["Volume"]) / recent_vol if recent_vol > 0 else 1.0
        except Exception:
            vol_ratio = 1.0
        trend_pct = (price / ema_val - 1) * 100
        sec_strong = sector_is_strong(ticker)

        pts = 0
        pts += 1 if trend_pct > 0.5 else 0     # clearly above its trend line
        pts += 1 if vol_ratio > 1.3 else 0     # above-average buying volume
        pts += 1 if sec_strong else 0          # its sector is trending up
        grade = "High" if pts >= 3 else ("Medium" if pts == 2 else "Low")

        rr = CONVICTION_RR.get(grade, RR_TARGET) if POSITION_MODE == "smart" else RR_TARGET
        stop   = price - ATR_STOP_MULT * atr_val
        risk   = price - stop
        target = price + rr * risk

        dte = days_to_earnings(ticker)
        headlines = get_headlines(ticker) if POSITION_MODE == "smart" else []
        return {
            "ticker": ticker,
            "currency": ticker_currency(ticker),
            "price": price,
            "stop": stop,
            "target": target,
            "rr": rr,
            "rsi_prev": rsi_prev,
            "rsi_now": rsi_now,
            "stop_pct": (stop - price) / price * 100,
            "target_pct": (target - price) / price * 100,
            "days_to_earnings": dte,
            "grade": grade,
            "vol_ratio": vol_ratio,
            "trend_pct": trend_pct,
            "sector_strong": sec_strong,
            "headlines": headlines,
        }
    return None


# ----------------------------------------------------------------------------
# TELEGRAM + FORMATTING
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
    if a["currency"] != "USD":
        return ""  # already EUR/GBP
    rate = get_eurusd()
    if not rate:
        return "\n<i>(EUR rate unavailable — use the % levels below)</i>"
    return (
        f"\n≈ EUR  entry {a['price'] / rate:.2f} · stop {a['stop'] / rate:.2f} "
        f"· target {a['target'] / rate:.2f}  <i>(approx, TR adds a small spread)</i>"
    )


def _earnings_line(a: dict) -> str:
    dte = a.get("days_to_earnings")
    if dte is not None and dte <= EARNINGS_WARN_DAYS:
        return f"\n⚠️ <b>Earnings in {dte} day(s)</b> — high gap risk, size small or skip."
    return ""


def _grade_emoji(g: str) -> str:
    return {"High": "🟢 High", "Medium": "🟡 Medium", "Low": "🟠 Low"}.get(g, g)


def _context_line(a: dict) -> str:
    bits = []
    vr = a.get("vol_ratio")
    if vr and vr >= 1.3:
        bits.append(f"volume {vr:.1f}× avg 🔺")
    elif vr:
        bits.append(f"volume {vr:.1f}× avg")
    ss = a.get("sector_strong")
    if ss is True:
        bits.append("sector strong")
    elif ss is False:
        bits.append("sector weak")
    out = ""
    if bits:
        out += "\n📊 " + " · ".join(bits)
    heads = a.get("headlines") or []
    if heads:
        out += "\n📰 " + " | ".join(h[:70] for h in heads[:2])
        out += "\n<i>Headlines are context — check before trading.</i>"
    return out


def _size_line(a: dict) -> str:
    ps = position_size(a)
    if not ps:
        return ""
    cur = a["currency"]
    if ps["mode"] == "smart":
        return (
            f"\n⭐ Conviction: <b>{_grade_emoji(a['grade'])}</b> "
            f"(trend {a['trend_pct']:+.1f}% · {'vol↑' if a.get('vol_ratio',0)>=1.3 else 'vol normal'}"
            f"{' · sector↑' if a.get('sector_strong') else ''})\n"
            f"💰 Suggested invest <b>€{ps['pos_val']:.0f}</b> (~{ps['shares']:.1f} shares)\n"
            f"🎯 Target <b>{a['target']:.2f} {cur}</b> ({a['target_pct']:+.1f}%, {a['rr']:.1f}R)\n"
            f"🛑 Safety stop {a['stop']:.2f} {cur} ({a['stop_pct']:+.1f}%) → risks €{ps['risk_eur']:.0f}"
            f"{_context_line(a)}"
        )
    if ps["mode"] == "fixed":
        return (
            f"\n💰 Invest <b>€{ps['pos_val']:.0f}</b> (~{ps['shares']:.1f} shares)\n"
            f"🎯 Take +€{PROFIT_TARGET_EUR:.0f} at <b>{ps['take_native']:.2f} {cur}</b> "
            f"(+{ps['take_gain_pct']:.1f}%)\n"
            f"🛑 Safety stop {a['stop']:.2f} {cur} ({a['stop_pct']:+.1f}%) "
            f"→ risks €{ps['risk_eur']:.0f} if hit"
        )
    # risk mode
    line = (
        f"\n💰 Invest ≈ <b>€{ps['pos_val']:.0f}</b> (~{ps['shares']:.1f} shares) "
        f"· risk €{ps['risk_eur']:.0f} if stopped ({RISK_PCT:.0f}% of €{ACCOUNT_EUR:.0f})"
    )
    if ps.get("capped"):
        line += ("\n<i>(tight stop → position capped to your cash, real risk only "
                 "€{:.0f}. No leverage.)</i>".format(ps["risk_eur"]))
    return line


def format_alert(a: dict) -> str:
    cur = a["currency"]
    head = (
        f"🔔 <b>{a['ticker']}</b> long setup ({INTERVAL})\n"
        f"Buy-the-dip in uptrend · RSI turned up from {a['rsi_prev']:.0f} → {a['rsi_now']:.0f}\n\n"
        f"Entry ~ <b>{a['price']:.2f} {cur}</b>"
    )
    if POSITION_MODE == "smart":
        return (
            head
            + _eur_line(a)
            + _size_line(a)
            + _earnings_line(a)
            + "\n\n<i>Size &amp; target scaled to setup quality. You decide. Not financial advice.</i>"
        )

    if POSITION_MODE == "fixed":
        # Your habit: fixed €3000 in, take +€100, with a safety stop.
        return (
            head
            + _eur_line(a)
            + _size_line(a)
            + _earnings_line(a)
            + f"\n\n<i>Strategy's own target would be {a['target']:.2f} {cur} "
              f"({a['target_pct']:+.1f}%) — further than +€100. "
              f"You decide. Not financial advice.</i>"
        )
    # risk mode: show full strategy levels
    return (
        head + "\n"
        f"Stop   {a['stop']:.2f} {cur}  ({a['stop_pct']:+.1f}%)\n"
        f"Target {a['target']:.2f} {cur}  ({a['target_pct']:+.1f}%)"
        f"{_eur_line(a)}"
        f"{_size_line(a)}"
        f"{_earnings_line(a)}\n\n"
        f"<b>Apply in Trade Republic using the % levels</b> — "
        f"stop {a['stop_pct']:+.1f}%, target {a['target_pct']:+.1f}% from your fill.\n"
        f"<i>You decide and place the trade. Not financial advice.</i>"
    )


# ----------------------------------------------------------------------------
# ALERT LOG  (permanent record for later review)
# ----------------------------------------------------------------------------

def log_alert(a: dict):
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp_utc", "ticker", "currency", "entry",
                        "stop", "target", "stop_pct", "target_pct",
                        "rsi_prev", "rsi_now", "days_to_earnings"])
        w.writerow([
            dt.datetime.utcnow().isoformat(timespec="seconds"),
            a["ticker"], a["currency"], f"{a['price']:.4f}",
            f"{a['stop']:.4f}", f"{a['target']:.4f}",
            f"{a['stop_pct']:.2f}", f"{a['target_pct']:.2f}",
            f"{a['rsi_prev']:.1f}", f"{a['rsi_now']:.1f}",
            a.get("days_to_earnings"),
        ])


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    print(f"Scanning {len(WATCHLIST)} tickers on {INTERVAL}...")

    if not market_is_healthy():
        print("Market regime WEAK — long dip-buys suppressed this run. Done.")
        return

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
        log_alert(a)

    # Warn if alerts cluster (concentration risk from correlated names).
    if len(alerts) >= 4:
        send_telegram(
            f"⚠️ <b>{len(alerts)} setups fired at once</b> "
            f"({', '.join(x['ticker'] for x in alerts)}).\n"
            f"These often move together — avoid taking all of them. Pick the best 1–2."
        )

    print(f"Done. {len(alerts)} alert(s) sent.")


if __name__ == "__main__":
    main()
