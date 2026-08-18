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
    # --- Expanded set (2026-08-12) — more liquid, Trade-Republic-buyable names ---
    # US
    "UBER", "ABNB", "PANW", "CRWD", "SNOW", "ANET", "TXN", "AMGN", "GILD",
    "PFE", "DE", "LMT", "SBUX", "LOW", "PYPL", "COIN",
    # Europe
    "RHM.DE", "MBG.DE", "BMW.DE", "VOW3.DE", "ADS.DE", "DBK.DE", "BAS.DE",
    "BAYN.DE", "ENR.DE", "DHL.DE", "MUV2.DE", "AZN.L", "NESN.SW", "TTE.PA",
    "SAN.PA", "ADYEN.AS",
]
# Trimmed illiquid / hyper-speculative names are listed in TRIMMED_STOCKS.md
# (kept out on purpose — see that file for the reasons).

# If watchlist.txt exists, it OVERRIDES the list above (so you can edit the
# watchlist from Telegram with /add and /remove without touching the code).
WATCHLIST_FILE = "watchlist.txt"


def load_watchlist():
    """Read tickers from WATCHLIST_FILE if present; else use the built-in list."""
    if not os.path.exists(WATCHLIST_FILE):
        return list(WATCHLIST)
    out = []
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line.upper())
    # de-duplicate, keep order
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t); uniq.append(t)
    return uniq or list(WATCHLIST)


WATCHLIST = load_watchlist()

# Strategy parameters
INTERVAL          = "15m"   # candle size
LOOKBACK          = "5d"    # history to pull (15m data limited to ~60d)
EMA_TREND         = 50      # trend filter length
RSI_LEN           = 14
RSI_OVERSOLD      = 35      # pullback threshold
ATR_LEN           = 14
ATR_STOP_MULT     = 1.5     # stop = entry - 1.5 * ATR
RR_TARGET         = 3.0     # NORMAL mode target (multi-day/week holds)
RR_FAST           = 1.5     # FAST mode target (hours–2 day holds; higher hit-rate)
# WIDE mode, added 2026-08-13 after the stop-width test (27336 setups). Identical
# entries, three exit plans — a wider stop nearly TRIPLED average return because a
# 1.5xATR stop treats ordinary noise as a failed trade:
#   1.5 ATR / 1.5R / 5d  -> +0.055 R   (falling+oversold 57% win / +0.174)
#   2.5 ATR / 3R  / 15d  -> +0.128 R   (falling+oversold 58% win / +0.216)
#   3.0 ATR / 4R  / 25d  -> +0.153 R   (falling+oversold 55% win / +0.241)
# Same 1% risk per trade either way — the position size shrinks as the stop widens,
# so this is a real gain in expectancy, paid for with a longer hold.
RR_WIDE           = 3.0
ATR_STOP_WIDE     = 2.5
EXTENDED_HOURS    = True     # include pre-/after-market bars (you trade extended
                            # hours on Trade Republic) — keeps prices current, but
                            # off-session volume is thin so signals are a bit noisier

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
ACCOUNT_EUR       = 10000.0  # default account size (overridden by ACCOUNT_EUR secret or /account)
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


# Human-readable company names (shown next to tickers so you know what you're
# looking at when you open Trade Republic). Covers the watchlist + sector names.
NAMES = {
    "SNDK": "SanDisk",
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq 100 ETF",
    "NVDA": "Nvidia", "AAPL": "Apple", "MSFT": "Microsoft", "AMZN": "Amazon",
    "GOOGL": "Alphabet (Google)", "META": "Meta", "AVGO": "Broadcom",
    "TSM": "TSMC", "AMD": "AMD", "QCOM": "Qualcomm", "MU": "Micron",
    "ORCL": "Oracle", "CRM": "Salesforce", "ADBE": "Adobe", "NOW": "ServiceNow",
    "INTU": "Intuit", "NFLX": "Netflix", "TSLA": "Tesla", "PLTR": "Palantir",
    "IBM": "IBM", "DELL": "Dell", "ARM": "Arm Holdings", "MRVL": "Marvell",
    "INTC": "Intel", "LLY": "Eli Lilly", "NVO": "Novo Nordisk", "KO": "Coca-Cola",
    "PEP": "PepsiCo", "COST": "Costco", "WMT": "Walmart", "MCD": "McDonald's",
    "V": "Visa", "MA": "Mastercard", "JPM": "JPMorgan", "UNH": "UnitedHealth",
    "HD": "Home Depot", "XOM": "ExxonMobil", "CVX": "Chevron",
    "BSX": "Boston Scientific", "JD": "JD.com", "FICO": "Fair Isaac",
    "SAP.DE": "SAP", "SIE.DE": "Siemens", "ASML.AS": "ASML", "AIR.DE": "Airbus",
    "ALV.DE": "Allianz", "DTE.DE": "Deutsche Telekom", "IFX.DE": "Infineon",
    "SHELL.AS": "Shell", "MC.PA": "LVMH", "OR.PA": "L'Oréal", "BY6.F": "BYD",
    # extra sector-drilldown names
    "DIS": "Disney", "TMUS": "T-Mobile US", "VZ": "Verizon", "NKE": "Nike",
    "BKNG": "Booking", "PG": "Procter & Gamble", "PM": "Philip Morris",
    "COP": "ConocoPhillips", "SLB": "Schlumberger", "EOG": "EOG Resources",
    "WMB": "Williams", "BAC": "Bank of America", "WFC": "Wells Fargo",
    "GS": "Goldman Sachs", "MS": "Morgan Stanley", "JNJ": "Johnson & Johnson",
    "ABBV": "AbbVie", "MRK": "Merck", "TMO": "Thermo Fisher", "GE": "GE Aerospace",
    "CAT": "Caterpillar", "HON": "Honeywell", "UNP": "Union Pacific",
    "BA": "Boeing", "RTX": "RTX (Raytheon)", "LIN": "Linde",
    "SHW": "Sherwin-Williams", "FCX": "Freeport-McMoRan", "NEM": "Newmont",
    "APD": "Air Products", "ECL": "Ecolab", "PLD": "Prologis",
    "AMT": "American Tower", "EQIX": "Equinix", "WELL": "Welltower",
    "SPG": "Simon Property", "O": "Realty Income", "NEE": "NextEra Energy",
    "DUK": "Duke Energy", "SO": "Southern Co", "D": "Dominion", "AEP": "American Electric",
    "EXC": "Exelon",
    # trimmed names (shown in /rank as higher-risk opportunities)
    "IREN": "IREN", "IONQ": "IonQ", "QBTS": "D-Wave", "RGTI": "Rigetti",
    "NVTS": "Navitas", "CRML": "Critical Metals", "RCAT": "Red Cat",
    "SNEX": "StoneX", "NBIS": "Nebius", "RBRK": "Rubrik", "ALAB": "Astera Labs",
    "SPCX": "SpaceX", "XK4.F": "Gabler Group", "SSIT.L": "Seraphim Space",
    "RPI.L": "Raspberry Pi", "SMHN.DE": "SÜSS MicroTec", "GXI.DE": "Gerresheimer",
    "MUX.DE": "Mutares", "SDF.DE": "K+S", "CRWV": "CoreWeave", "SHOP": "Shopify",
    "ZS": "Zscaler", "TEAM": "Atlassian", "UPST": "Upstart", "PODD": "Insulet",
    "RDDT": "Reddit", "HOOD": "Robinhood", "SOFI": "SoFi", "TTD": "The Trade Desk",
    "HPE": "HP Enterprise", "DDOG": "Datadog",
    # expanded watchlist US names
    "UBER": "Uber", "ABNB": "Airbnb", "PANW": "Palo Alto Networks",
    "CRWD": "CrowdStrike", "SNOW": "Snowflake", "ANET": "Arista Networks",
    "TXN": "Texas Instruments", "AMGN": "Amgen", "GILD": "Gilead",
    "PFE": "Pfizer", "DE": "Deere", "LMT": "Lockheed Martin",
    "SBUX": "Starbucks", "LOW": "Lowe's", "PYPL": "PayPal", "COIN": "Coinbase",
    # popular German / EU names (for /find — Trade Republic staples)
    "RHM.DE": "Rheinmetall", "MBG.DE": "Mercedes-Benz", "BMW.DE": "BMW",
    "VOW3.DE": "Volkswagen", "P911.DE": "Porsche AG", "PAH3.DE": "Porsche SE",
    "DBK.DE": "Deutsche Bank", "CBK.DE": "Commerzbank", "BAS.DE": "BASF",
    "BAYN.DE": "Bayer", "ADS.DE": "Adidas", "PUM.DE": "Puma", "DHL.DE": "DHL Group",
    "MRK.DE": "Merck KGaA", "MUV2.DE": "Munich Re", "DB1.DE": "Deutsche Börse",
    "EOAN.DE": "E.ON", "RWE.DE": "RWE", "VNA.DE": "Vonovia", "HEN3.DE": "Henkel",
    "CON.DE": "Continental", "ZAL.DE": "Zalando", "HFG.DE": "HelloFresh",
    "SY1.DE": "Symrise", "FRE.DE": "Fresenius", "QIA.DE": "Qiagen",
    "NEM.DE": "Nemetschek", "ENR.DE": "Siemens Energy", "HAG.DE": "Hensoldt",
    "STM.PA": "STMicroelectronics", "TTE.PA": "TotalEnergies", "BNP.PA": "BNP Paribas",
    "SU.PA": "Schneider Electric", "AI.PA": "Air Liquide", "SAN.PA": "Sanofi",
    "DG.PA": "Vinci", "RMS.PA": "Hermès", "KER.PA": "Kering", "STLAM.MI": "Stellantis",
    "ENEL.MI": "Enel", "ISP.MI": "Intesa Sanpaolo", "UCG.MI": "UniCredit",
    "ADYEN.AS": "Adyen", "PRX.AS": "Prosus", "INGA.AS": "ING Group",
    "NOVN.SW": "Novartis", "NESN.SW": "Nestlé", "ROG.SW": "Roche",
    "AZN.L": "AstraZeneca", "HSBA.L": "HSBC", "BP.L": "BP", "ULVR.L": "Unilever",
}


def name_for(ticker: str) -> str:
    """Company name for a ticker, or '' if unknown."""
    return NAMES.get(ticker.upper(), "")


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
# VWAP + SUPPORT + RELATIVE STRENGTH  (daily-data context for the fit score)
# ----------------------------------------------------------------------------
# These read a DAILY frame (not the 15m one) because support levels, the
# volume-weighted average price big investors watch, and strength-vs-the-market
# are longer-term context. They answer three plain questions:
#   VWAP           — are big buyers treating this price as cheap (supporting it)?
#   support        — is there a floor just below where buyers tend to step in?
#   rel. strength  — is this stock a leader (beating the market) or a laggard?

def rolling_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Volume-weighted average price over the last `window` days.
    The price level weighted by where volume actually traded."""
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    pv = tp * df["Volume"]
    return pv.rolling(window).sum() / df["Volume"].rolling(window).sum()


def anchored_vwap(df: pd.DataFrame, anchor_date) -> pd.Series:
    """VWAP measured from a specific date (e.g. last earnings) to now —
    the average price everyone who bought since that event is sitting at."""
    d = df.loc[anchor_date:]
    tp = (d["High"] + d["Low"] + d["Close"]) / 3.0
    pv = tp * d["Volume"]
    return pv.cumsum() / d["Volume"].cumsum()


def vwap_signal(daily: pd.DataFrame, price: float, window: int = 20) -> dict:
    """Classify price vs the ~1-month VWAP into one of the 'rubber band' states.
    Returns {dist (%), above, state, note}. States:
      bounce    — a GENUINE pullback: price was clearly above VWAP, has come back
                  DOWN to the line, and is TURNING UP again → high-odds re-entry
      above     — comfortably above VWAP (buyers in control, but no fresh pullback)
      far_above — stretched well above VWAP (rubber band; snap-back risk)
      broke     — fresh drop BELOW VWAP (trend may have broken → AVOID)
      below     — under VWAP (recent buyers underwater)
      chop      — crossing VWAP back and forth (directionless → AVOID)
    """
    res = {"dist": None, "above": None, "state": "unknown", "note": ""}
    if daily is None or len(daily) < window + 6:
        return res
    d = daily
    if isinstance(d.columns, pd.MultiIndex):
        d = d.copy(); d.columns = d.columns.get_level_values(0)
    d = d.dropna(subset=["Close"])
    try:
        vw = rolling_vwap(d, window)
        vwn = float(vw.iloc[-1])
    except Exception:
        return res
    if vwn != vwn or vwn <= 0:
        return res
    dist = (price - vwn) / vwn * 100.0
    res["dist"] = dist
    res["above"] = price >= vwn
    # close-vs-VWAP over the last ~15 bars → how many times it flipped sides
    diff = (d["Close"].iloc[-16:-1] - vw.iloc[-16:-1]).dropna()
    signs = [1 if x >= 0 else -1 for x in diff.values]
    flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    was_above = any(x > 0 for x in signs[-6:])

    # A real BOUNCE is a sequence, not a position: price must have actually come
    # DOWN and touched the VWAP line in the last few bars, and now be turning
    # back UP. Without this, a stock drifting near its highs a hair above VWAP
    # was mislabelled a "bounce" (the JNJ case).
    touched = False
    turning = False
    try:
        lows = d["Low"].iloc[-4:-1]
        vws = vw.iloc[-4:-1]
        touched = bool((lows <= vws * 1.005).any())      # reached/pierced the line
        c_now = float(d["Close"].iloc[-1]); c_prev = float(d["Close"].iloc[-2])
        turning = c_now > c_prev and price >= vwn        # turned back up, holding VWAP
    except Exception:
        pass

    if flips >= 4:
        res["state"] = "chop"; res["note"] = "chopping across VWAP — directionless, avoid"
    elif dist < -2 and was_above:
        res["state"] = "broke"; res["note"] = "broke below VWAP — trend may have broken, avoid"
    elif dist < -1:
        res["state"] = "below"; res["note"] = "under VWAP — recent buyers underwater"
    elif -1 <= dist <= 3 and touched and turning:
        res["state"] = "bounce"
        res["note"] = "pulled back TO VWAP and turned up — genuine dip-buy zone"
    elif -1 <= dist <= 3:
        res["state"] = "above"
        res["note"] = "near VWAP but no pullback-and-turn yet — wait for the bounce"
    elif dist > 8:
        res["state"] = "far_above"; res["note"] = "stretched above VWAP — snap-back risk"
    else:
        res["state"] = "above"; res["note"] = "above VWAP — buyers in control"
    return res


def earnings_avwap_signal(daily: pd.DataFrame, price: float, anchor_date) -> dict:
    """Anchored VWAP measured from the last earnings date = the average price of
    everyone who bought since that report (their 'break-even'). Classify price vs
    that line. Returns {avwap, dist, state, note, anchor}. States:
      above  — earnings buyers in profit; post-earnings trend intact
      bounce — back at their break-even from above; they defend here (re-entry)
      below  — earnings buyers underwater; the post-earnings move has failed
    """
    res = {"avwap": None, "dist": None, "state": "unknown", "note": "", "anchor": None}
    if daily is None or anchor_date is None:
        return res
    d = daily
    if isinstance(d.columns, pd.MultiIndex):
        d = d.copy(); d.columns = d.columns.get_level_values(0)
    d = d.dropna(subset=["Close"])
    try:
        a = pd.Timestamp(anchor_date)
        if a.tzinfo is not None:
            a = a.tz_localize(None)
        a = a.normalize()
        if d.index.tz is not None:
            a = a.tz_localize(d.index.tz)
        av = anchored_vwap(d, a)
        avn = float(av.iloc[-1])
    except Exception:
        return res
    if avn != avn or avn <= 0:
        return res
    dist = (price - avn) / avn * 100.0
    res.update({"avwap": avn, "dist": dist, "anchor": a})
    if dist < -1.5:
        res["state"] = "below"
        res["note"] = "below it — everyone who bought since the last earnings is underwater; the move has failed"
    elif dist <= 3:
        res["state"] = "bounce"
        res["note"] = "back at it from above — the earnings buyers defend their break-even here; strong re-entry"
    else:
        res["state"] = "above"
        res["note"] = "above it — the earnings buyers are in profit; post-earnings trend intact"
    return res


def last_earnings_date(ticker: str):
    """Most recent PAST earnings date (to anchor the earnings VWAP), or None."""
    try:
        cal = yf.Ticker(ticker).get_earnings_dates(limit=24)
        if cal is None or cal.empty:
            return None
        now = pd.Timestamp.now(tz=cal.index.tz)
        past = cal.index[cal.index < now]
        return past.max() if len(past) else None
    except Exception:
        return None


# --- Real support lines: tested bounce levels on weekly / monthly timeframes ---
# A support line is a price FLOOR the stock fell to and bounced from before — and
# the more times it was tested, and the longer the timeframe, the more it matters.
# Daily lows are too noisy, so we resample to WEEKLY and MONTHLY, find pivot lows
# (local bottoms), cluster nearby ones into one level, and count the touches.

def _resample_low_close(daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Down-sample a daily frame to weekly/monthly Low & Close."""
    return pd.DataFrame({
        "Low":   daily["Low"].resample(rule).min(),
        "Close": daily["Close"].resample(rule).last(),
    }).dropna()


def _pivot_lows(low: pd.Series, k: int):
    """Local-bottom prices: a bar whose Low is the lowest in a ±k window."""
    v = low.values
    n = len(v)
    return [float(v[i]) for i in range(k, n - k) if v[i] == v[i - k:i + k + 1].min()]


def _pivot_lows_ts(low: pd.Series, k: int):
    """Local bottoms as (position, price) — keeps the bar position so touches can
    be DEBOUNCED in time (the plain _pivot_lows throws the timestamps away)."""
    v = low.values
    n = len(v)
    return [(i, float(v[i])) for i in range(k, n - k)
            if v[i] == v[i - k:i + k + 1].min()]


def _count_tests(low: pd.Series, level: float, tol: float, exit_mult: float = 1.5):
    """How many DISTINCT times price came down and tested `level`.

    This is the fix for the "tested 34×" illusion: two hours of sideways drift
    inside the zone is ONE test, not 34. Price must LEAVE the zone (rise above
    it by exit_mult × the band) before a new visit is counted."""
    if not level or level <= 0:
        return 0
    band = level * tol
    top = level + band
    exit_at = level + band * exit_mult
    tests, inside = 0, False
    for x in low.values:
        if x != x:                       # NaN
            continue
        if not inside and x <= top:      # came down into the zone → one test
            tests += 1
            inside = True
        elif inside and x > exit_at:     # left the zone → ready for a new test
            inside = False
    return tests


def _support_zone(low: pd.Series, pivots, price: float, tol: float):
    """Nearest tested support zone BELOW the price, built from swing-low pivots
    and counted with DEBOUNCED tests. {level, touches, dist, pivots} or None."""
    below = [(i, l) for i, l in pivots if l and l == l and l < price * 0.999]
    if not below:
        return None
    clusters = []
    for _, lvl in sorted(below, key=lambda x: -x[1]):
        for c in clusters:
            if abs(lvl - c["lvl"]) / c["lvl"] <= tol:
                c["ls"].append(lvl); c["lvl"] = sum(c["ls"]) / len(c["ls"]); break
        else:
            clusters.append({"lvl": lvl, "ls": [lvl]})
    nr = max(clusters, key=lambda c: c["lvl"])      # highest = closest below
    lvl = nr["lvl"]
    return {"level": lvl, "touches": _count_tests(low, lvl, tol),
            "dist": (price - lvl) / price * 100.0, "pivots": len(nr["ls"])}


def tradeable_bars(df: pd.DataFrame):
    """Drop bars where NOTHING actually traded.

    The 15m frame is downloaded with pre/post-market included, and ~60% of those
    bars carry Volume=0 — they are quote artifacts, not trades. yfinance still
    stamps them with a Low/High, which invented phantom support levels ("tested
    4×" at a price no share changed hands at) and phantom range highs. Support is
    only meaningful where real volume defended a price."""
    if df is None or len(df) == 0:
        return df
    d = df
    if isinstance(d.columns, pd.MultiIndex):
        d = d.copy(); d.columns = d.columns.get_level_values(0)
    if "Volume" not in d.columns:
        return d
    live = d[d["Volume"].fillna(0) > 0]
    return live if len(live) >= 12 else d      # keep the frame usable if too thin


def _pivot_highs_ts(high: pd.Series, k: int):
    """Local tops as (position, price) — the mirror of _pivot_lows_ts."""
    v = high.values
    return [(i, float(v[i])) for i in range(k, len(v) - k)
            if v[i] == v[i - k:i + k + 1].max()]


def _resistance_above(pivots, price: float, tol: float = 0.015):
    """Nearest clustered swing-high ABOVE the price — the first ceiling a bounce
    would run into. {level, touches} or None."""
    above = [l for _, l in pivots if l and l == l and l > price * 1.001]
    if not above:
        return None
    clusters = []
    for lvl in sorted(above):
        for c in clusters:
            if abs(lvl - c["lvl"]) / c["lvl"] <= tol:
                c["ls"].append(lvl); c["lvl"] = sum(c["ls"]) / len(c["ls"]); break
        else:
            clusters.append({"lvl": lvl, "ls": [lvl]})
    nr = min(clusters, key=lambda c: c["lvl"])
    return {"level": nr["lvl"], "touches": len(nr["ls"])}


def upside_space(daily: pd.DataFrame, price: float, atr_val: float):
    """ROOM TO RUN before the next overhead resistance, measured in R (units of
    the 1.5xATR stop). This was the STRONGEST factor measured (15942 setups):
        room >= 2R  → 56% win / +0.179 R
        room <  2R  → 50% win / +0.053 R   (0-0.5R was the worst at +0.034)
    A perfect dip at support is still a bad trade if a ceiling sits just above it,
    because the target simply cannot be reached. Returns
    {level, room_r, pct, touches} or None when there is no resistance overhead
    (i.e. clear blue sky above — treated as maximum room)."""
    if daily is None or not atr_val or atr_val <= 0 or len(daily) < 40:
        return None
    d = daily
    if isinstance(d.columns, pd.MultiIndex):
        d = d.copy(); d.columns = d.columns.get_level_values(0)
    d = d.dropna(subset=["High"]).tail(126)          # ~6 months, same as support
    try:
        piv = _pivot_highs_ts(d["High"], 4)
        # A pivot needs 4 bars either side to confirm, so the MOST RECENT high —
        # usually the peak price just fell away from, i.e. the resistance that
        # matters most — is structurally invisible. Add it back as a candidate.
        recent = d["High"].tail(12)
        if len(recent):
            piv = piv + [(len(d) - 1, float(recent.max()))]
        res = _resistance_above(piv, price)
    except Exception:
        return None
    if not res:
        return {"level": None, "room_r": 99.0, "pct": None, "touches": 0}
    room = (res["level"] - price) / (ATR_STOP_MULT * atr_val)
    return {"level": res["level"], "room_r": room,
            "pct": (res["level"] - price) / price * 100.0,
            "touches": res["touches"]}


def trend_structure(daily: pd.DataFrame, bars: int = 45, k: int = 3, tol: float = 0.005):
    """TREND BY PRICE STRUCTURE — higher highs + higher lows.

    An uptrend is not "price is higher than a month ago". It is a repeating
    pattern: push to a new peak, pull back to a HIGHER low than the last dip,
    then break the old peak — buyers stepping in earlier each time. It also
    breaks honestly: the moment price makes a LOWER low the uptrend is over,
    even if the month is still green (the Amazon case: +4% on the month while
    making lower highs and lower lows for two weeks).

    `tol` ignores tiny wiggles so noise doesn't break a real trend.
    Returns {state, hh, hl, last_high, last_low, broke_low, note}."""
    out = {"state": "unclear", "hh": None, "hl": None, "last_high": None,
           "last_low": None, "broke_low": False, "note": ""}
    try:
        d = daily
        if isinstance(d.columns, pd.MultiIndex):
            d = d.copy(); d.columns = d.columns.get_level_values(0)
        d = d.dropna(subset=["High", "Low", "Close"]).tail(bars)
        if len(d) < 20:
            return out
        hs = [p for _, p in _pivot_highs_ts(d["High"], k)]
        ls = [p for _, p in _pivot_lows_ts(d["Low"], k)]
        price = float(d["Close"].iloc[-1])
        # The newest swing high can't be pivot-confirmed (it needs k bars after
        # it), so fold in the running high of the last stretch.
        recent_hi = float(d["High"].tail(k + 2).max())
        if not hs or recent_hi > hs[-1] * (1 + tol):
            hs = hs + [recent_hi]
        if len(hs) < 2 or len(ls) < 2:
            return out
        # The textbook definition is "a line through TWO or more successive higher
        # lows". Demanding THREE was stricter than that and labelled obvious
        # uptrends (NBIS, PLTR — up 28-46% in two weeks) as "sideways". So judge
        # on the last TWO swings; a third that agrees only CONFIRMS it.
        hs, ls = hs[-3:], ls[-3:]
        out["last_high"], out["last_low"] = hs[-1], ls[-1]

        def rising(seq):
            return seq[-1] > seq[-2] * (1 - tol)

        def falling(seq):
            return seq[-1] < seq[-2] * (1 + tol)

        hh, hl = rising(hs), rising(ls)
        lh, ll = falling(hs), falling(ls)
        out["confirmed"] = bool(
            hh and hl and len(hs) >= 3 and len(ls) >= 3
            and hs[-2] > hs[-3] * (1 - tol) and ls[-2] > ls[-3] * (1 - tol))
        out["hh"], out["hl"] = hh, hl
        out["broke_low"] = price < ls[-1] * (1 - tol)   # structure just failed

        if hh and hl and not out["broke_low"]:
            out["state"] = "uptrend"
            out["note"] = "higher highs and higher lows — buyers in control"
        elif hh and hl:
            out["state"] = "uptrend_broken"
            out["note"] = ("was climbing, but price dropped below its last higher "
                           "low — the pattern just broke")
        elif lh and ll:
            out["state"] = "downtrend"
            out["note"] = "lower highs and lower lows — sellers in control"
        elif hl and not hh:
            out["state"] = "stalling"
            out["note"] = ("still making higher lows but no new highs — the climb "
                           "is losing steam")
        elif lh and not ll:
            out["state"] = "basing"
            out["note"] = "lower highs but holding its lows — building a base"
        else:
            out["state"] = "sideways"
            out["note"] = "no clear higher-high / higher-low pattern"
    except Exception:
        pass
    return out


def range_position(df: pd.DataFrame):
    """Where the current price sits inside its recent high-low range, 0-100.
    0 = at the lows (a real pullback), 100 = at the highs (chasing).
    Returns (position_%, range_width_%) or (None, None)."""
    try:
        d = df
        if isinstance(d.columns, pd.MultiIndex):
            d = d.copy(); d.columns = d.columns.get_level_values(0)
        lo = float(d["Low"].min()); hi = float(d["High"].max())
        px = float(d["Close"].dropna().iloc[-1])
        if not (hi > lo > 0):
            return None, None
        return (px - lo) / (hi - lo) * 100.0, (hi / lo - 1) * 100.0
    except Exception:
        return None, None


def _nearest_support(levels, price, tol=0.03):
    """Cluster pivot lows (within tol%) and return the nearest cluster BELOW the
    price: {level, touches, dist_%}. None if there is no support below."""
    below = [l for l in levels if l and l == l and l < price * 0.999]
    if not below:
        return None
    clusters = []
    for lvl in sorted(below, reverse=True):
        for c in clusters:
            if abs(lvl - c["lvl"]) / c["lvl"] <= tol:
                c["ls"].append(lvl); c["lvl"] = sum(c["ls"]) / len(c["ls"]); break
        else:
            clusters.append({"lvl": lvl, "ls": [lvl]})
    nr = max(clusters, key=lambda c: c["lvl"])          # highest = closest below
    return {"level": nr["lvl"], "touches": len(nr["ls"]),
            "dist": (price - nr["lvl"]) / price * 100.0}


def _strongest_support(levels, price, tol=0.04):
    """The MOST-TESTED cluster below the price (the big long-term floor):
    {level, touches, dist}. Ties → the closer one. None if nothing below."""
    below = [l for l in levels if l and l == l and l < price * 0.999]
    if not below:
        return None
    clusters = []
    for lvl in sorted(below, reverse=True):
        for c in clusters:
            if abs(lvl - c["lvl"]) / c["lvl"] <= tol:
                c["ls"].append(lvl); c["lvl"] = sum(c["ls"]) / len(c["ls"]); break
        else:
            clusters.append({"lvl": lvl, "ls": [lvl]})
    best = max(clusters, key=lambda c: (len(c["ls"]), c["lvl"]))
    return {"level": best["lvl"], "touches": len(best["ls"]),
            "dist": (price - best["lvl"]) / price * 100.0}


def support_levels(daily: pd.DataFrame, price: float, intraday=None) -> dict:
    """Nearest tested support floor below the price on THREE horizons. Ranges
    tightened 2026-08-13 (per Grace) so a floor is only shown while it's still
    RELEVANT to how the stock trades now — an old 2-year low isn't:
      short  — LIVE INTRADAY: is the price right NOW at/near a recent intraday
               floor (from the 15-min frame, ~last 5 days) — not daily candles
      medium — daily pivots, last ~6 months   (swing structure — the main one)
      long   — weekly pivots, last ~1 year      (major, stronger floor)
    Each is {level, touches, dist} or None. Higher timeframe = stronger."""
    out = {"short": None, "medium": None, "long": None}
    if daily is None or len(daily) < 40:
        return out
    d = daily
    if isinstance(d.columns, pd.MultiIndex):
        d = d.copy(); d.columns = d.columns.get_level_values(0)
    d = d.dropna(subset=["Low", "Close"])
    if d.empty:
        return out
    try:
        # SHORT-TERM = live intraday, last ~3 SESSIONS only (a support line drawn
        # across a whole week of 15m bars is meaningless). Tight 0.6% clustering
        # so a single consolidation isn't smeared into one giant "zone", and the
        # PREVIOUS DAY LOW is added as a candidate — it's a level traders watch.
        if intraday is not None and len(intraday) > 12:
            idf = intraday
            if isinstance(idf.columns, pd.MultiIndex):
                idf = idf.copy(); idf.columns = idf.columns.get_level_values(0)
            idf = idf.dropna(subset=["Low"])
            idf = tradeable_bars(idf)          # real trades only — no dead bars
            if isinstance(idf.index, pd.DatetimeIndex):
                # Full trading WEEK (5 sessions) — this matches how the chart is
                # read by eye ("is it touching a floor 2-3× this week, with
                # clearly higher prices earlier in the week?"), so the bot's
                # verdict and the 1-week chart agree. Levels further than 2 ATR
                # are hidden anyway, so a wider window only improves the touch
                # count, it doesn't drag in stale levels.
                sessions = sorted(set(idf.index.date))
                if len(sessions) > 5:
                    keep = set(sessions[-5:])
                    idf = idf[[dd in keep for dd in idf.index.date]]
                    prev = idf[[dd == sessions[-2] for dd in idf.index.date]] \
                        if len(sessions) >= 2 else None
                else:
                    prev = None
            else:
                prev = None
            piv = _pivot_lows_ts(idf["Low"], 4)
            if prev is not None and len(prev):
                piv.append((0, float(prev["Low"].min())))     # previous-day low
            out["short"] = _support_zone(idf["Low"], piv, price, tol=0.006)
        else:
            piv = _pivot_lows_ts(d["Low"].tail(22), 2)
            out["short"] = _support_zone(d["Low"].tail(22), piv, price, tol=0.01)
    except Exception:
        pass
    try:
        # ~6 months of daily bars (swing structure — the main one).
        sl = d["Low"].tail(126)
        out["medium"] = _support_zone(sl, _pivot_lows_ts(sl, 4), price, tol=0.015)
    except Exception:
        pass
    try:
        # ~1 year, resampled to weekly (smooths noise for the major floor).
        wk = _resample_low_close(d.tail(252), "W")
        out["long"] = _support_zone(wk["Low"], _pivot_lows_ts(wk["Low"], 3),
                                    price, tol=0.025)
    except Exception:
        pass
    return out


# How strongly to reward being AT each horizon's tested floor (higher TF = more).
# Weights bumped 2026-08-12 after the backtest showed at-support was the single
# strongest factor (+0.08R vs -0.18R when not at support). (bonus_at, bonus_near)
# Scaled up 2026-08-13: at-tested-support was the ONLY factor with a real edge in
# the 6217-setup test (+0.102 R vs -0.003 R away), so it now carries the largest
# share of the score.
#
# Proximity is measured in ATR (volatility) units, NOT a flat % — 1% means far
# more for a quiet stock than a wild one. Backtest (6214 setups), distance to a
# tested floor in ATR units:  0.5-0.75 ATR +0.164 R (best) · within 1 ATR +0.112
# · 1.5-2 ATR +0.056 · 2-3 ATR -0.005 · 3+ ATR -0.128.  So ~1 ATR = "at it",
# ~2 ATR = the outer edge of any edge, beyond that it is noise and we hide it.
# High-vol names benefit most (within-1-ATR +0.111 vs flat-3% +0.096).
# Short-term intraday floors should sit closer, so they use tighter multiples
# (0.5 ATR ≈ 1.1% on a typical stock — matching the "~1%" intuition).
# (key, label, bonus_at, bonus_near, atr_at, atr_near)
_SUPPORT_TIERS = [
    ("long",   "long-term",   24.0, 12.0, 1.0, 2.0),
    ("medium", "medium-term", 19.0,  9.5, 1.0, 2.0),
    ("short",  "short-term",  13.0,  6.5, 0.5, 1.0),
]
SUPPORT_HIDE_ATR = 2.0     # further than this → not shown at all


# Range-position credit, CALIBRATED FROM THE BACKTEST (6217 setups, 5y):
# at tested support & low in range  → +0.121 R
# at tested support & high in range → +0.091 R
# Both are strongly positive, so being high in the range is a MILD demerit, not a
# disqualifier — an earlier hard block would have thrown away 1625 profitable
# setups. Ratio 0.091/0.121 ≈ 0.75 sets RANGE_POS_HIGH_FACTOR.
RANGE_POS_GOOD = 40.0          # full credit at/below this % of the recent range
RANGE_POS_MAX  = 55.0          # above this: reduced credit (NOT blocked)
RANGE_POS_HIGH_FACTOR = 0.75   # evidence-calibrated, not a guess
MIN_RANGE_PCT  = 2.0           # below this the range is too tight to read (noted only)


def support_summary(levels: dict, range_pos=None, range_pct=None) -> dict:
    """Which tested support the price is currently AT/near, across horizons.
    Returns {bonus, tag, tier, dist, touches, note}.

    A level needs 2+ DEBOUNCED tests to count as real support — the backtest
    showed 2+ is the sweet spot (more touches did NOT improve results).
    Range position scales the credit (see constants above) but never blocks:
    at-support setups pay off even when price is high in its range."""
    best = {"bonus": 0.0, "tag": None, "tier": None, "dist": None,
            "touches": None, "note": None}

    factor = 1.0
    if range_pos is not None:
        if range_pos >= RANGE_POS_MAX:
            factor = RANGE_POS_HIGH_FACTOR
        elif range_pos > RANGE_POS_GOOD:
            span = (range_pos - RANGE_POS_GOOD) / (RANGE_POS_MAX - RANGE_POS_GOOD)
            factor = 1.0 - span * (1.0 - RANGE_POS_HIGH_FACTOR)
    if range_pct is not None and range_pct < MIN_RANGE_PCT:
        best["note"] = "very tight range — levels are less meaningful here"

    for key, label, pts_at, pts_near, atr_at, atr_near in _SUPPORT_TIERS:
        lv = (levels or {}).get(key)
        if not lv:
            continue
        # Distance in ATR units when we know the stock's volatility; fall back to
        # the old flat-% thresholds only if ATR is unavailable.
        da = lv.get("dist_atr")
        tested = lv["touches"] >= 2
        # "AT SUPPORT" is reserved for the setup that is actually rare and
        # valuable: a tested floor AND price pulled back into it. Proximity alone
        # fired on 59% of the universe — more often than a plain RSI dip (27%),
        # which is backwards. Requiring the pullback makes it ~22% and it scored
        # better too (+0.121 R vs +0.102 R). A tested floor underneath while
        # price sits HIGH in its range is kept, but never framed as a dip-buy.
        pulled_back = (range_pos is None) or (range_pos <= RANGE_POS_MAX)
        if da is not None:
            # Distance relative to this tier's "at" threshold, so the WORDS match
            # what the chart shows. Saying "at support" when price is 0.85 ATR
            # above the line (SLB) is simply wrong — and, awkwardly, the BEST
            # performing zone (+0.164 R) is exactly where price has lifted OFF
            # the line, so the strong setups were the ones being misnamed.
            rel = da / atr_at if atr_at else da
            if rel < 0.25:
                word, mult = f"sitting on the {label} support line (not bounced yet)", 0.6
            elif rel <= 0.75:
                word, mult = f"at {label} support", 1.0
            elif rel <= 1.5:
                word, mult = f"just above {label} support", 0.8
            elif rel <= 2.0:
                word, mult = f"near {label} support", 0.5
            else:
                continue
            if not tested:
                if rel <= 0.75:
                    cand, tag = 1.5, f"at weak {label} low"
                else:
                    continue
            elif pulled_back:
                cand, tag = pts_at * mult, word
            else:
                cand, tag = pts_at * mult * 0.5, f"above a {label} level (not a dip)"
        elif lv["dist"] <= 3 and tested:
            cand, tag = pts_at, f"at {label} support"
        elif lv["dist"] <= 5 and tested:
            cand, tag = pts_near, f"near {label} support"
        elif lv["dist"] <= 3:
            cand, tag = 1.5, f"at weak {label} low"
        else:
            continue
        cand *= factor
        if cand > best["bonus"]:
            best = {"bonus": cand, "tag": tag, "tier": label,
                    "dist": lv["dist"], "touches": lv["touches"],
                    "note": best.get("note")}
    return best


# SPY daily frame cached once per run (benchmark for relative strength).
_spy_daily_cache = {"df": None}


def _spy_daily():
    if _spy_daily_cache["df"] is not None:
        return _spy_daily_cache["df"]
    try:
        m = yf.download(MARKET_INDEX, period="1y", interval="1d",
                        progress=False, auto_adjust=False)
        if isinstance(m.columns, pd.MultiIndex):
            m.columns = m.columns.get_level_values(0)
        _spy_daily_cache["df"] = m
    except Exception:
        _spy_daily_cache["df"] = None
    return _spy_daily_cache["df"]


def _spy_return(lookback: int):
    m = _spy_daily()
    try:
        return (float(m["Close"].iloc[-1]) / float(m["Close"].iloc[-lookback]) - 1) * 100.0
    except Exception:
        return None


def relative_strength(df: pd.DataFrame, lookback: int = 21):
    """Stock's %% move minus the market's over `lookback` days (~1 month).
    > 0 = beating the market (a leader, where money is flowing); < 0 = lagging."""
    try:
        sret = (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-lookback]) - 1) * 100.0
    except Exception:
        return None
    spy = _spy_return(lookback)
    return None if spy is None else sret - spy


def daily_context(ticker: str, ddf: pd.DataFrame, price: float, intraday=None) -> dict:
    """Bundle VWAP / support / relative-strength for a ticker from its daily frame.
    `intraday` (the 15m frame) is used for the LIVE short-term support horizon."""
    ctx = {"vwap": None, "above_vwap": None, "vwap_state": None, "vwap_dist": None,
           "vwap_note": None, "support_levels": None,
           "support_bonus": 0.0, "support_tag": None, "support_tier": None,
           "support_dist": None, "support_touches": None, "rel_strength": None,
           "atr_daily": None, "range_pos": None, "range_pct": None,
           "support_note": None, "upside": None, "trend_heat": None}
    if ddf is None or len(ddf) < 20:
        return ctx
    if isinstance(ddf.columns, pd.MultiIndex):
        ddf = ddf.copy()
        ddf.columns = ddf.columns.get_level_values(0)
    ddf = ddf.dropna(subset=["Close"])
    if ddf.empty:
        return ctx
    # Daily ATR — a realistic stop distance for a swing trade (the 15m ATR is
    # distorted tiny during thin pre-/after-market hours).
    try:
        ctx["atr_daily"] = float(atr(ddf, ATR_LEN).iloc[-1])
    except Exception:
        pass
    try:
        sig = vwap_signal(ddf, price, 20)
        ctx["vwap_state"] = sig["state"]
        ctx["vwap_dist"] = sig["dist"]
        ctx["vwap_note"] = sig["note"]
        ctx["above_vwap"] = sig["above"]
        if sig["dist"] is not None:
            ctx["vwap"] = price / (1 + sig["dist"] / 100.0)
    except Exception:
        pass
    try:
        sl = support_levels(ddf, price, intraday=intraday)
        # Express each level's distance in ATR units so "close" scales with the
        # stock's own volatility (1% is huge for KO, routine for NVDA).
        _a = ctx.get("atr_daily")
        if _a and _a > 0:
            for _lv in sl.values():
                if _lv:
                    _lv["dist_atr"] = (price - _lv["level"]) / _a
        ctx["support_levels"] = sl
        # Where price sits in its RECENT range — from the intraday frame (last
        # ~5 sessions) when we have it, else the last 10 daily bars. This is the
        # guard that stops "at support" firing on a stock sitting at its highs.
        # Range must also ignore zero-volume bars — a phantom after-hours high
        # made price look like it was sitting at the top of its range.
        rp, rw = (range_position(tradeable_bars(intraday))
                  if intraday is not None and len(intraday) > 12
                  else range_position(ddf.tail(10)))
        ctx["range_pos"], ctx["range_pct"] = rp, rw
        summ = support_summary(sl, range_pos=rp, range_pct=rw)
        ctx["support_note"] = summ.get("note")
    except Exception:
        pass
    try:
        ctx["upside"] = upside_space(ddf, price, ctx.get("atr_daily"))
    except Exception:
        pass
    try:
        # Short-horizon trend context (1 week / 1 month, plus how STEADY the climb
        # is). Shown so the dip can be judged against the bigger picture; only the
        # 1-week leg feeds the score, because it is the only one that tested
        # positive next to a dip.
        c = ddf["Close"].dropna()
        th = {"week": None, "wk2": None, "month": None, "mon2": None,
              "smooth": None, "rsi_pct": None, "pull_atr": None}
        if len(c) > 6:
            th["week"] = (price / float(c.iloc[-6]) - 1) * 100.0
        if len(c) > 11:
            th["wk2"] = (price / float(c.iloc[-11]) - 1) * 100.0
        if len(c) > 22:
            th["month"] = (price / float(c.iloc[-22]) - 1) * 100.0
        if len(c) > 43:
            th["mon2"] = (price / float(c.iloc[-43]) - 1) * 100.0
        if len(c) > 21:
            y = [float(v) for v in c.iloc[-21:].values]
            n = len(y); xs = list(range(n))
            mx = sum(xs) / n; my = sum(y) / n
            sx = sum((a - mx) ** 2 for a in xs) ** .5
            sy = sum((b - my) ** 2 for b in y) ** .5
            if sx and sy:
                th["smooth"] = sum((a - mx) * (b - my) for a, b in zip(xs, y)) / (sx * sy)
        # How far price has pulled back from its 10-day high, in ATR — a
        # VOLATILITY-RELATIVE depth, so a "shallow dip" means the same thing on a
        # quiet stock and a wild one.
        av = ctx.get("atr_daily")
        if av and av > 0 and len(c) > 10:
            th["pull_atr"] = (float(c.iloc[-10:].max()) - price) / av
        # RSI measured against its OWN recent range instead of a fixed number.
        # This is the fix that made Grace's "buy the dip inside a climb" idea
        # testable: a clean uptrend never reaches RSI 45, but it DOES drop to the
        # bottom of its own range, and that is where the edge showed up
        # (steady climb + RSI in its own lower third = 52% win / +0.109 R).
        try:
            rs_ser = rsi(c, RSI_LEN).dropna()
            if len(rs_ser) > 20:
                w = rs_ser.iloc[-20:]
                lo, hi = float(w.min()), float(w.max())
                if hi > lo:
                    th["rsi_pct"] = (float(rs_ser.iloc[-1]) - lo) / (hi - lo) * 100.0
        except Exception:
            pass
        th["struct"] = trend_structure(ddf)
        ctx["trend_heat"] = th
        ctx["support_bonus"] = summ["bonus"]
        ctx["support_tag"] = summ["tag"]
        ctx["support_tier"] = summ["tier"]
        ctx["support_dist"] = summ["dist"]
        ctx["support_touches"] = summ["touches"]
    except Exception:
        pass
    try:
        ctx["rel_strength"] = relative_strength(ddf, 21)
    except Exception:
        pass
    return ctx


# ----------------------------------------------------------------------------
# ACCOUNT SIZE + 1% RISK CALCULATOR  (the position-sizing safety net)
# ----------------------------------------------------------------------------
# The single biggest fix for drawdowns: never risk more than a small, fixed %%
# of the account on one trade. Given the stop distance, this back-computes how
# much to actually buy so a stop-out loses only that %%. Trade Republic has no
# API, so the account size is set by you (via /account) and stored here.

ACCOUNT_FILE = "account.txt"


def load_account() -> float:
    """Account size in EUR. Priority: ACCOUNT_EUR secret (private, permanent) →
    account.txt (set from chat, lives only in the running session) → default.
    We deliberately do NOT commit account.txt: the repo is public and your
    account size is private."""
    env = os.environ.get("ACCOUNT_EUR")
    if env:
        try:
            v = float(env)
            if v > 0:
                return v
        except Exception:
            pass
    try:
        if os.path.exists(ACCOUNT_FILE):
            with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
                v = float(f.read().strip())
                if v > 0:
                    return v
    except Exception:
        pass
    return ACCOUNT_EUR


def save_account(value: float):
    with open(ACCOUNT_FILE, "w", encoding="utf-8") as f:
        f.write(str(int(round(value))))


# --- Trade mode: same ranking, different TARGET/horizon --------------------
# "normal" = 3R target, days-to-weeks.  "fast" = 1.5R target, hours-to-2-days
# (backtest: higher win-rate, fits quick trading). The score/ranking is identical
# in both — only the target and suggested hold time change.
MODE_FILE = "mode.txt"


def load_mode() -> str:
    env = os.environ.get("TRADE_MODE", "").strip().lower()
    if env in ("fast", "normal", "wide"):
        return env
    try:
        if os.path.exists(MODE_FILE):
            m = open(MODE_FILE, "r", encoding="utf-8").read().strip().lower()
            if m in ("fast", "normal", "wide"):
                return m
    except Exception:
        pass
    return "normal"


def save_mode(mode: str):
    with open(MODE_FILE, "w", encoding="utf-8") as f:
        f.write(mode.strip().lower())


def active_rr() -> float:
    """Reward:risk target for the current mode."""
    m = load_mode()
    if m == "fast":
        return RR_FAST
    if m == "wide":
        return RR_WIDE
    return RR_TARGET


def active_stop_mult() -> float:
    """ATR multiple for the stop. WIDE mode gives the trade room to breathe —
    the tested reason its expectancy is ~2.3x the tight plan's."""
    return ATR_STOP_WIDE if load_mode() == "wide" else ATR_STOP_MULT


def mode_horizon() -> str:
    m = load_mode()
    if m == "fast":
        return "hours–2 days"
    if m == "wide":
        return "1–3 weeks (give it room)"
    return "days–weeks"


def risk_position(price: float, currency: str, stop_pct: float,
                  account_eur: float = None, risk_pct: float = None):
    """1%-rule sizing: how much to buy so a stop-out loses ~risk_pct of the account.
    Returns shares, position value, and the € at risk. No leverage (position capped
    to the account). None if inputs are unusable."""
    entry_eur = to_eur(price, currency)
    if not entry_eur or entry_eur <= 0:
        return None
    acct = account_eur if account_eur else load_account()
    rp = risk_pct if risk_pct else RISK_PCT
    per_share_risk = entry_eur * abs(stop_pct) / 100.0
    if acct <= 0 or rp <= 0 or per_share_risk <= 0:
        return None
    budget = acct * rp / 100.0
    shares = budget / per_share_risk
    pos_val = shares * entry_eur
    capped = False
    if not ALLOW_LEVERAGE and pos_val > acct:
        shares = acct / entry_eur
        pos_val = acct
        capped = True
    return {"shares": shares, "pos_val": pos_val, "risk_eur": shares * per_share_risk,
            "account": acct, "risk_pct": rp, "capped": capped, "entry_eur": entry_eur}


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

# 2026-08-13: 40% of the rank universe (63 names) had NO sector, so most /rank
# cards showed no sector line at all. Filled in below.
# NOTE ON EU NAMES: sector strength is measured from the US SPDR sector ETFs, so
# for European tickers the sector NAME is right but the daily % is a US proxy for
# that sector's rotation — close enough to answer "is my sector hot today", not a
# measurement of the European sector itself.
SECTOR_MAP.update({
    "SNDK": "Technology",
    # --- US technology & software
    "TXN": "Technology", "ANET": "Technology", "PANW": "Technology",
    "CRWD": "Technology", "SNOW": "Technology", "ZS": "Technology",
    "TEAM": "Technology", "DDOG": "Technology", "SHOP": "Technology",
    "HPE": "Technology", "ALAB": "Technology", "RBRK": "Technology",
    "NBIS": "Technology", "CRWV": "Technology", "IREN": "Technology",
    "IONQ": "Technology", "QBTS": "Technology", "RGTI": "Technology",
    "NVTS": "Technology", "TTD": "Technology",
    # --- US health care
    "AMGN": "Health Care", "GILD": "Health Care", "PFE": "Health Care",
    "PODD": "Health Care",
    # --- US financials (PYPL/COIN/HOOD/SOFI/UPST are GICS Financials)
    "PYPL": "Financials", "COIN": "Financials", "HOOD": "Financials",
    "SOFI": "Financials", "UPST": "Financials", "SNEX": "Financials",
    # --- US industrials / consumer / comms
    "DE": "Industrials", "LMT": "Industrials", "UBER": "Industrials",
    "RCAT": "Industrials",
    "SBUX": "Consumer Disc.", "LOW": "Consumer Disc.", "ABNB": "Consumer Disc.",
    "RDDT": "Comm. Services",
    "CRML": "Materials",
    # --- Europe (sector name accurate; % is the US sector ETF as proxy)
    "RHM.DE": "Industrials", "ENR.DE": "Industrials", "DHL.DE": "Industrials",
    "MBG.DE": "Consumer Disc.", "BMW.DE": "Consumer Disc.",
    "VOW3.DE": "Consumer Disc.", "ADS.DE": "Consumer Disc.",
    "DBK.DE": "Financials", "MUV2.DE": "Financials", "ADYEN.AS": "Financials",
    "BAS.DE": "Materials", "SDF.DE": "Materials",
    "BAYN.DE": "Health Care", "AZN.L": "Health Care", "SAN.PA": "Health Care",
    "GXI.DE": "Health Care",
    "NESN.SW": "Consumer Staples",
    "TTE.PA": "Energy",
    "SMHN.DE": "Technology",
})

# Example constituents per sector (liquid US large-caps, all buyable on Trade
# Republic). Used for the /sector drill-down AND to widen the /rank universe so
# it surfaces opportunities beyond your personal watchlist.
SECTOR_HOLDINGS = {
    "Technology":       ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "AMD"],
    "Comm. Services":   ["GOOGL", "META", "NFLX", "DIS", "TMUS", "VZ"],
    "Consumer Disc.":   ["AMZN", "TSLA", "HD", "MCD", "NKE", "BKNG"],
    "Consumer Staples": ["WMT", "COST", "PG", "KO", "PEP", "PM"],
    "Energy":           ["XOM", "CVX", "COP", "SLB", "EOG", "WMB"],
    "Financials":       ["JPM", "BAC", "WFC", "GS", "MS", "V"],
    "Health Care":      ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO"],
    "Industrials":      ["GE", "CAT", "HON", "UNP", "BA", "RTX"],
    "Materials":        ["LIN", "SHW", "FCX", "NEM", "APD", "ECL"],
    "Real Estate":      ["PLD", "AMT", "EQIX", "WELL", "SPG", "O"],
    "Utilities":        ["NEE", "DUK", "SO", "D", "AEP", "EXC"],
}

# Make sure every constituent has a sector mapping (for conviction/scoring).
for _sec, _tks in SECTOR_HOLDINGS.items():
    for _tk in _tks:
        SECTOR_MAP.setdefault(_tk, _sec)

# Previously-trimmed names — kept OUT of the live scan/watchlist (liquidity /
# volatility / data-quality reasons) but INCLUDED in the /rank universe so you
# still see them as opportunities. Higher risk; scoring penalties apply as usual.
TRIMMED = [
    # thin / small-cap / speculative
    "IREN", "IONQ", "QBTS", "RGTI", "NVTS", "CRML", "RCAT", "SNEX", "NBIS",
    "RBRK", "ALAB", "SPCX", "XK4.F", "SSIT.L", "RPI.L", "SMHN.DE", "GXI.DE",
    "MUX.DE", "SDF.DE",
    # liquid but choppy / whipsaw-prone
    "CRWV", "SHOP", "ZS", "TEAM", "UPST", "PODD", "RDDT", "HOOD", "SOFI",
    "TTD", "HPE", "DDOG",
]


def rank_universe():
    """Watchlist (minus index ETFs) + sector constituents + trimmed names,
    de-duplicated. This is the pool /rank analyses — the widest opportunity set."""
    seen, out = set(), []
    pool = (list(WATCHLIST)
            + [x for v in SECTOR_HOLDINGS.values() for x in v]
            + list(TRIMMED))
    for t in pool:
        if t in ("SPY", "QQQ"):
            continue
        if t not in seen:
            seen.add(t); out.append(t)
    return out


_sector_cache = {"data": None}


def refresh_caches():
    """Clear the per-session caches (market/SPY, sector strength, FX) so the next
    call re-downloads them. The long-poll bot stays alive ~5.7h and would
    otherwise reuse these — call this on each manual /rank and /score so results
    are as fresh as Yahoo allows (~15-min delayed feed)."""
    _fx_cache["EURUSD=X"] = None
    _fx_cache["EURGBP=X"] = None
    _spy_daily_cache["df"] = None
    _sector_cache["data"] = None


def get_sector_strength():
    """Return {sector: {'day': %, 'week': %, 'month': %, 'strong': bool}} once per run.

    'day' is TODAY's move (sector rotation changes daily — it's what tells you
    where money is flowing right now); 'month'/'strong' are the slower trend used
    for scoring, because that is the definition that was backtested."""
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
            prev = float(close.iloc[-2])
            week_ago = float(close.iloc[-6])
            month_ago = float(close.iloc[-21])
            sma50 = float(close.rolling(50).mean().iloc[-1])
            out[name] = {"day": (last - prev) / prev * 100,
                         "week": (last - week_ago) / week_ago * 100,
                         "month": (last - month_ago) / month_ago * 100,
                         "strong": last > sma50}
        except Exception:
            continue
    _sector_cache["data"] = out
    return out


def sector_day_ranking():
    """Sectors ordered by TODAY's move, best first: [(name, day_%), …].
    This is the daily heat-map — where money is rotating right now."""
    data = get_sector_strength()
    return sorted(((n, d.get("day", 0.0)) for n, d in data.items()),
                  key=lambda x: x[1], reverse=True)


def sector_info(ticker: str):
    """{name, day, week, month, strong, day_rank, of} for a ticker's sector.
    day_rank = 1 means the hottest sector today. None if unknown."""
    sec = SECTOR_MAP.get(ticker)
    if not sec:
        return None
    d = get_sector_strength().get(sec)
    if not d:
        return None
    order = [n for n, _ in sector_day_ranking()]
    rank = order.index(sec) + 1 if sec in order else None
    return {"name": sec, "day_rank": rank, "of": len(order), **d}


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

def next_earnings_ts(ticker: str):
    """Next earnings date/time as a tz-aware Europe/Berlin Timestamp, or None.

    yfinance stores the timestamp in US/Eastern. We convert it to Berlin so the
    date AND time are shown in the user's own timezone. NOTE: the TIME is reliable
    for US names but only approximate for EU/UK names (yfinance uses a placeholder
    Eastern time for them); the DATE is dependable either way."""
    try:
        cal = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if cal is None or cal.empty:
            return None
        idx = cal.index
        now = pd.Timestamp.now(tz=idx.tz)
        future = idx[idx > now]
        if len(future) == 0:
            return None
        ts = future.min()
        try:
            return ts.tz_convert("Europe/Berlin")
        except Exception:
            return ts
    except Exception:
        return None


def days_to_earnings(ticker: str):
    """Calendar days until the next earnings, in Europe/Berlin, or None.

    Uses the CALENDAR-DATE difference in Berlin (not a raw hour-count): a report
    tomorrow morning is 'in 1 day' even when it's under 24h away, so 'today'
    never leaks onto an event that is actually the next Berlin date."""
    ts = next_earnings_ts(ticker)
    if ts is None:
        return None
    today = dt.datetime.now(ZoneInfo("Europe/Berlin")).date()
    return (ts.date() - today).days


# ----------------------------------------------------------------------------
# SIGNAL LOGIC
# ----------------------------------------------------------------------------

def check_ticker(ticker: str):
    """Return an alert dict if a setup fires on the latest candle, else None."""
    try:
        df = yf.download(ticker, period=LOOKBACK, interval=INTERVAL,
                         progress=False, auto_adjust=False, prepost=EXTENDED_HOURS)
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
    nm = name_for(a["ticker"])
    title = f"{a['ticker']}" + (f" · {nm}" if nm else "")
    head = (
        f"🔔 <b>{title}</b> long setup ({INTERVAL})\n"
        f"Buy-the-dip in uptrend · RSI turned up from {a['rsi_prev']:.0f} → {a['rsi_now']:.0f}\n\n"
        f"Entry ~ <b>{a['price']:.2f} {cur}</b>"
    )
    if POSITION_MODE == "smart":
        return (
            head
            + _eur_line(a)
            + _size_line(a)
            + _earnings_line(a)
            + "\n\n<i>Size &amp; target scaled to setup quality.</i>"
        )

    if POSITION_MODE == "fixed":
        # Your habit: fixed €3000 in, take +€100, with a safety stop.
        return (
            head
            + _eur_line(a)
            + _size_line(a)
            + _earnings_line(a)
            + f"\n\n<i>Strategy's own target would be {a['target']:.2f} {cur} "
              f"({a['target_pct']:+.1f}%) — further than +€100.</i>"
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
        f"stop {a['stop_pct']:+.1f}%, target {a['target_pct']:+.1f}% from your fill."
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
