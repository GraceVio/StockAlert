"""
Conviction backtest — do higher-graded setups actually win more?
----------------------------------------------------------------
Replays history, grades each signal by the SAME measurable factors the live bot
uses (trend strength + volume surge), and reports win rate / expectancy per
grade. If High > Medium > Low, the grading adds real value.

(Sector strength is excluded here — it needs point-in-time sector data that's
costly to reconstruct. This tests the two stock-level factors, which drive most
of the grade.)

Run:  python conviction_backtest.py
"""

import pandas as pd
import yfinance as yf
import scanner as s

WATCHLIST     = s.WATCHLIST
BACKTEST_DAYS = "60d"
RR            = 3.0
MAX_HOLD_BARS = 100


def run():
    buckets = {"Low": [], "Medium": [], "High": []}
    for t in WATCHLIST:
        try:
            df = yf.download(t, period=BACKTEST_DAYS, interval="15m",
                             progress=False, auto_adjust=False)
        except Exception:
            continue
        if df is None or len(df) < 70:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df["EMA"] = s.ema(df["Close"], 50)
        df["RSI"] = s.rsi(df["Close"], 14)
        df["ATR"] = s.atr(df, 14)
        df["VMA"] = df["Volume"].rolling(20).mean()
        df = df.dropna()
        c = df["Close"].values; h = df["High"].values; l = df["Low"].values
        e = df["EMA"].values; r = df["RSI"].values; a = df["ATR"].values
        v = df["Volume"].values; vma = df["VMA"].values
        n = len(c); i = 1
        while i < n - 1:
            if c[i] > e[i] and r[i-1] <= 35 and r[i] > r[i-1]:
                trend_pct = (c[i] / e[i] - 1) * 100
                vol_ratio = v[i] / vma[i] if vma[i] > 0 else 1.0
                pts = (1 if trend_pct > 0.5 else 0) + (1 if vol_ratio > 1.3 else 0)
                grade = "High" if pts == 2 else ("Medium" if pts == 1 else "Low")
                entry = c[i]; stop = entry - 1.5 * a[i]; target = entry + RR * (entry - stop)
                for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, n)):
                    if l[j] <= stop:
                        buckets[grade].append(-1.0); i = j; break
                    if h[j] >= target:
                        buckets[grade].append(RR); i = j; break
                i += 1
            i += 1

    print(f"\n{'Grade':>7} | {'trades':>6} {'win%':>6} {'expectancy(R)':>13}")
    print("-" * 40)
    for g in ["High", "Medium", "Low"]:
        x = buckets[g]
        if not x:
            print(f"{g:>7} |    n/a")
            continue
        wins = sum(1 for v_ in x if v_ > 0)
        exp = sum(x) / len(x)
        print(f"{g:>7} | {len(x):>6} {wins/len(x)*100:>5.1f}% {exp:>+13.2f}")
    print("\nIf expectancy rises High > Medium > Low, the grading is adding value.")
    print("Small ~60-day sample — directional, not precise.")


if __name__ == "__main__":
    run()
