"""
Pattern Study - objective search for repeatable tendencies
----------------------------------------------------------
Analyses ~8 years of DAILY history for well-known statistical effects and
reports what (if anything) actually shows up, WITH sample sizes so you can
judge whether it is signal or noise.

It tests:
  1. Monthly seasonality      - average return in each calendar month
  2. Day-of-week effect       - average return by weekday
  3. Mean reversion           - average NEXT-day return after a big down day
  4. Momentum / follow-through - average NEXT-day return after 3 up days in a row

HONEST FRAMING (read this):
  Decades of research show stock prices are *mostly* not reliably predictable
  from past prices alone. Real edges that exist (momentum, mean-reversion,
  seasonality) are SMALL, UNSTABLE, and tend to weaken once widely known.
  This tool measures tendencies objectively - it does NOT predict the future.
  A tendency that shows a +0.1% average with a huge spread is noise, not a
  money machine. Use findings as gentle tilts, never as certainty.

Run:  python pattern_study.py
"""

import numpy as np
import pandas as pd
import yfinance as yf

TICKERS = ["SPY", "NVDA", "AAPL", "MSFT", "AMZN"]   # edit freely
YEARS   = "8y"


def load(ticker):
    df = yf.download(ticker, period=YEARS, interval="1d",
                     progress=False, auto_adjust=True)
    if df is None or len(df) < 250:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df["ret"] = df["Close"].pct_change() * 100
    return df.dropna()


def describe(name, sample):
    """Return a one-line honest description of a return sample."""
    if len(sample) < 20:
        return f"{name}: too few samples ({len(sample)}) — ignore"
    avg = np.mean(sample)
    win = np.mean([1 for x in sample if x > 0]) * 100 if sample else 0
    win = np.mean((np.array(sample) > 0)) * 100
    std = np.std(sample)
    # signal check: is the average meaningfully bigger than the noise?
    strength = "noise" if abs(avg) < std / (len(sample) ** 0.5) * 2 else "notable"
    return (f"{name}: avg {avg:+.2f}% · {win:.0f}% up · "
            f"n={len(sample)} · [{strength}]")


def study_ticker(ticker):
    df = load(ticker)
    if df is None:
        print(f"\n{ticker}: not enough data")
        return
    ret = df["ret"]

    print(f"\n{'='*60}\n{ticker} — {len(df)} trading days\n{'='*60}")

    # 1. Monthly seasonality
    print("Monthly seasonality (avg daily return within each month):")
    by_month = df.groupby(df.index.month)["ret"].mean()
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    best = by_month.idxmax(); worst = by_month.idxmin()
    print(f"  best month: {months[best-1]} ({by_month[best]:+.2f}%/day) · "
          f"worst: {months[worst-1]} ({by_month[worst]:+.2f}%/day)")

    # 2. Day-of-week
    print("Day-of-week effect:")
    days = ["Mon","Tue","Wed","Thu","Fri"]
    by_dow = df.groupby(df.index.dayofweek)["ret"].mean()
    for d in range(5):
        if d in by_dow.index:
            print(f"  {days[d]}: {by_dow[d]:+.3f}% avg")

    # 3. Mean reversion after a big down day (< -2%)
    big_down_next = df["ret"].shift(-1)[df["ret"] < -2.0].dropna().tolist()
    print(describe("  After a -2% day, NEXT day", big_down_next))

    # 4. Momentum after 3 up days in a row
    up = (df["ret"] > 0).astype(int)
    three_up = (up.rolling(3).sum() == 3)
    mom_next = df["ret"].shift(-1)[three_up].dropna().tolist()
    print(describe("  After 3 up days, NEXT day", mom_next))


def main():
    print("PATTERN STUDY — objective tendencies, not predictions\n"
          "Anything marked [noise] is not a reliable edge.")
    for t in TICKERS:
        study_ticker(t)
    print("\nReminder: these are historical tendencies. Markets change, edges "
          "decay, and past behaviour does not guarantee the future.\n")


if __name__ == "__main__":
    main()
