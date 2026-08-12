"""
Score validation backtest
--------------------------
Two jobs:
  1. Does a higher 0-100 score mean a better outcome? (score bands)
  2. Which TARGET suits which holding style? (compare RR/horizon configs)

It replays ~3y of DAILY dip-in-uptrend setups, scores each with the EXACT live
formula (rank_today._score_100), stores the forward price path, then evaluates
several (target, horizon) plans on the SAME setups so they are directly
comparable — e.g. FAST (1.5R, 5 days) vs NORMAL (3R, 20 days).

Honest limits: daily candles (live core is 15m, no long history); ~3y; no
slippage/spread; sector factor neutral. Validates the RANKING, not a profit promise.

Run:  python score_backtest.py            # console report
      python score_backtest.py --send     # also send to Telegram
"""

import sys
import numpy as np
import pandas as pd
import yfinance as yf
import scanner as s
import rank_today as rk

ATR_MULT = s.ATR_STOP_MULT   # 1.5
YEARS = "3y"
MAXH = 25                    # forward bars stored per setup
BT_RSI = 45                  # daily-appropriate pullback (15m RSI<=35 ~never fires daily)

# Trade plans to compare: (label, target_R, horizon_days)
CONFIGS = [("FAST 1.5R/5d", 1.5, 5), ("MED 2R/10d", 2.0, 10), ("NORMAL 3R/20d", 3.0, 20)]

BT_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "AMD", "QCOM",
    "ORCL", "CRM", "NFLX", "TSLA", "COST", "WMT", "KO", "PEP", "MCD", "V",
    "MA", "JPM", "UNH", "HD", "XOM", "CVX", "LLY", "DIS", "NKE", "SBUX", "LMT",
]


def _spy_frame():
    m = yf.download("SPY", period=YEARS, interval="1d", progress=False, auto_adjust=False)
    if isinstance(m.columns, pd.MultiIndex):
        m.columns = m.columns.get_level_values(0)
    return m.dropna(subset=["Close"])


def backtest_ticker(tk, spy):
    d = yf.download(tk, period=YEARS, interval="1d", progress=False, auto_adjust=False)
    if d is None or len(d) < 260:
        return []
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.dropna(subset=["Close"])
    close = d["Close"]
    ema = s.ema(close, s.EMA_TREND)
    rsi = s.rsi(close, s.RSI_LEN)
    atr = s.atr(d, s.ATR_LEN)
    vwap = s.rolling_vwap(d, 20)
    spy_close = spy["Close"].reindex(d.index).ffill()
    spy_sma50 = spy_close.rolling(50).mean()
    ret21 = close / close.shift(21) - 1
    spy_ret21 = spy_close / spy_close.shift(21) - 1
    rs_series = (ret21 - spy_ret21) * 100

    # Earnings dates (once per ticker) to anchor the earnings-VWAP per setup.
    try:
        ec = yf.Ticker(tk).get_earnings_dates(limit=30)
        edates = sorted(pd.Timestamp(x).tz_localize(None).normalize()
                        for x in ec.index) if ec is not None and not ec.empty else []
    except Exception:
        edates = []

    highs, lows, closes = d["High"].values, d["Low"].values, close.values
    vol = d["Volume"].values
    idx = d.index
    n = len(d)
    out = []
    for i in range(60, n - 1):
        try:
            if not (closes[i] > ema.iloc[i]):
                continue
            if not (rsi.iloc[i - 1] <= BT_RSI and rsi.iloc[i] > rsi.iloc[i - 1]):
                continue
            price = float(closes[i]); atr_val = float(atr.iloc[i])
            if atr_val <= 0 or np.isnan(atr_val):
                continue
            healthy = True
            if not np.isnan(spy_sma50.iloc[i]):
                healthy = bool(spy_close.iloc[i] > spy_sma50.iloc[i])
            av = None if np.isnan(vwap.iloc[i]) else bool(price >= vwap.iloc[i])
            rv = float(vol[i - 21:i].mean())
            vr = float(vol[i]) / rv if rv > 0 else 1.0
            summ = s.support_summary(s.support_levels(d.iloc[:i + 1], price))
            vst = s.vwap_signal(d.iloc[:i + 1], price)["state"]
            bar_date = pd.Timestamp(idx[i]).tz_localize(None).normalize()
            anchor = None
            for e in edates:
                if e <= bar_date:
                    anchor = e
                else:
                    break
            est = (s.earnings_avwap_signal(d.iloc[:i + 1], price, anchor)["state"]
                   if anchor is not None else "none")
            rsx = None if np.isnan(rs_series.iloc[i]) else float(rs_series.iloc[i])
            score, _, _ = rk._score_100(price, float(ema.iloc[i]), float(rsi.iloc[i]),
                                        float(rsi.iloc[i - 1]), vr, None, healthy,
                                        vwap_state=vst, rel_strength=rsx,
                                        support_bonus=summ["bonus"], support_tag=summ["tag"])
            out.append({"ticker": tk, "date": idx[i], "score": score, "price": price,
                        "atr": atr_val,
                        "fh": highs[i + 1:i + 1 + MAXH].tolist(),
                        "fl": lows[i + 1:i + 1 + MAXH].tolist(),
                        "fc": closes[i + 1:i + 1 + MAXH].tolist(),
                        "above_vwap": av, "at_support": summ["bonus"] > 0,
                        "leader": (rsx is not None and rsx >= 5), "vwap_state": vst,
                        "eavwap_state": est})
        except Exception:
            continue
    return out


def _eval(row, rr, horizon):
    """Outcome in R for a given target/horizon, from the stored forward path."""
    price = row["price"]; stop = price - ATR_MULT * row["atr"]; risk = price - stop
    if risk <= 0:
        return 0.0
    target = price + rr * risk
    fh, fl, fc = row["fh"][:horizon], row["fl"][:horizon], row["fc"][:horizon]
    for j in range(len(fh)):
        if fl[j] <= stop:
            return -1.0
        if fh[j] >= target:
            return rr
    return (fc[-1] - price) / risk if fc else 0.0


def _band(sc):
    if sc >= 70:
        return "70-100 (strong)"
    if sc >= 60:
        return "60-69 (good)"
    if sc >= 45:
        return "45-59 (watch)"
    return "0-44 (weak)"


def run(tickers=None, send=False):
    tickers = tickers or BT_TICKERS
    spy = _spy_frame()
    setups = []
    for tk in tickers:
        t = backtest_ticker(tk, spy)
        setups.extend(t)
        print(f"  {tk}: {len(t)} setups")
    if not setups:
        print("No setups found.")
        return None
    df = pd.DataFrame(setups)
    L = [f"Score validation — {len(df)} dip setups, {len(tickers)} stocks, {YEARS} daily", ""]

    # 1) Trade-plan comparison (same setups, different targets)
    L.append("TARGET COMPARISON (all setups):")
    L.append(f"{'plan':<16}{'win%':>7}{'exp/trade (R)':>15}")
    for label, rr, hz in CONFIGS:
        r = df.apply(lambda x: _eval(x, rr, hz), axis=1)
        L.append(f"{label:<16}{(r > 0).mean()*100:>6.0f}%{r.mean():>15.2f}")

    # 2) Score bands under the FAST plan and the NORMAL plan
    for label, rr, hz in [CONFIGS[0], CONFIGS[-1]]:
        df["R"] = df.apply(lambda x: _eval(x, rr, hz), axis=1)
        L.append("")
        L.append(f"SCORE BANDS — {label}:")
        L.append(f"{'band':<18}{'n':>5}{'win%':>7}{'exp (R)':>9}")
        for b in ["70-100 (strong)", "60-69 (good)", "45-59 (watch)", "0-44 (weak)"]:
            sub = df[df["score"].map(_band) == b]
            if len(sub):
                L.append(f"{b:<18}{len(sub):>5}{sub['win'] if False else (sub['R']>0).mean()*100:>6.0f}%{sub['R'].mean():>9.2f}")
        L.append(f"score↔outcome corr: {df['score'].corr(df['R']):+.3f}")

    # 3) VWAP-state test (under the FAST plan — Grace's style)
    df["R"] = df.apply(lambda x: _eval(x, CONFIGS[0][1], CONFIGS[0][2]), axis=1)
    L.append("")
    L.append(f"VWAP-STATE test — {CONFIGS[0][0]} (does each scenario behave as claimed?):")
    L.append(f"{'state':<12}{'n':>5}{'win%':>7}{'exp (R)':>9}")
    for st in ["bounce", "above", "far_above", "below", "broke", "chop"]:
        sub = df[df["vwap_state"] == st]
        if len(sub):
            L.append(f"{st:<12}{len(sub):>5}{(sub['R']>0).mean()*100:>6.0f}%{sub['R'].mean():>9.2f}")

    # 4) Earnings-AVWAP state test (under FAST)
    L.append("")
    L.append("EARNINGS-VWAP test — FAST (price vs the last-earnings break-even):")
    L.append(f"{'state':<10}{'n':>5}{'win%':>7}{'exp (R)':>9}")
    for st in ["above", "bounce", "below", "none"]:
        sub = df[df["eavwap_state"] == st]
        if len(sub):
            L.append(f"{st:<10}{len(sub):>5}{(sub['R']>0).mean()*100:>6.0f}%{sub['R'].mean():>9.2f}")

    report = "\n".join(L)
    print(report.encode("ascii", "replace").decode("ascii"))
    if send:
        s.send_telegram("<b>📊 Score backtest</b>\n<pre>" + report + "</pre>")
    return df


if __name__ == "__main__":
    run(send="--send" in sys.argv)
