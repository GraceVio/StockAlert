"""
Parameter Optimizer
-------------------
Tests many combinations of the strategy's decision parameters over recent
history and reports which settings held up best — so we tune from evidence,
not guesswork.

To stay fast and fair it downloads each ticker's data ONCE, computes the
indicators ONCE (fixed lengths), then re-simulates every parameter combo on
that same data. Only the DECISION thresholds are swept:

  RSI_OVERSOLD   in  {30, 35, 40}     (how deep a dip to require)
  ATR_STOP_MULT  in  {1.0, 1.5, 2.0}  (stop distance)
  RR_TARGET      in  {1.5, 2.0, 2.5, 3.0}  (reward vs risk)

OVERFITTING WARNING (read this):
  The "best" combo on 60 days of history is partly luck. Do NOT just grab the
  top line. Look for a STABLE REGION — settings whose neighbours also score
  well. A lone spike surrounded by poor scores is noise and will fail live.

Run:  python optimize.py
"""

import itertools
import pandas as pd
import yfinance as yf
import scanner as s

WATCHLIST     = s.WATCHLIST
INTERVAL      = s.INTERVAL
BACKTEST_DAYS = "60d"
MAX_HOLD_BARS = 100

RSI_GRID = [30, 35, 40]
STOP_GRID = [1.0, 1.5, 2.0]
RR_GRID = [1.5, 2.0, 2.5, 3.0]


def load_all():
    """Download once, precompute indicators once. Returns list of arrays per ticker."""
    data = []
    for t in WATCHLIST:
        try:
            df = yf.download(t, period=BACKTEST_DAYS, interval=INTERVAL,
                             progress=False, auto_adjust=False)
        except Exception:
            continue
        if df is None or len(df) < s.EMA_TREND + 20:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df["EMA"] = s.ema(df["Close"], s.EMA_TREND)
        df["RSI"] = s.rsi(df["Close"], s.RSI_LEN)
        df["ATR"] = s.atr(df, s.ATR_LEN)
        df = df.dropna()
        data.append((
            df["Close"].values, df["High"].values, df["Low"].values,
            df["EMA"].values, df["RSI"].values, df["ATR"].values,
        ))
    return data


def simulate(data, rsi_os, stop_mult, rr):
    trades = []
    for close, high, low, emav, rsiv, atrv in data:
        n = len(close)
        i = 1
        while i < n - 1:
            if close[i] > emav[i] and rsiv[i - 1] <= rsi_os and rsiv[i] > rsiv[i - 1]:
                entry = close[i]
                stop = entry - stop_mult * atrv[i]
                target = entry + rr * (entry - stop)
                outcome = None
                for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, n)):
                    if low[j] <= stop:
                        outcome = -1.0; i = j; break
                    if high[j] >= target:
                        outcome = rr; i = j; break
                if outcome is not None:
                    trades.append(outcome)
                else:
                    i += MAX_HOLD_BARS
            i += 1
    return trades


def stats(trades):
    n = len(trades)
    if n == 0:
        return None
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses) or 1e-9
    return {
        "n": n,
        "win_rate": len(wins) / n * 100,
        "pf": gross_win / gross_loss,
        "exp": sum(trades) / n,
    }


def main():
    print(f"Optimizing over {len(WATCHLIST)} tickers ({BACKTEST_DAYS}, {INTERVAL})...")
    data = load_all()
    print(f"Loaded {len(data)} tickers. Testing "
          f"{len(RSI_GRID)*len(STOP_GRID)*len(RR_GRID)} combos...\n")

    results = []
    for rsi_os, stop_mult, rr in itertools.product(RSI_GRID, STOP_GRID, RR_GRID):
        st = stats(simulate(data, rsi_os, stop_mult, rr))
        if st and st["n"] >= 20:   # ignore combos with too few trades
            results.append((rsi_os, stop_mult, rr, st))

    # rank by expectancy (R per trade)
    results.sort(key=lambda r: r[3]["exp"], reverse=True)

    print(f"{'RSI':>4} {'stop':>5} {'RR':>4} | {'trades':>6} {'win%':>6} "
          f"{'PF':>6} {'exp(R)':>7}")
    print("-" * 48)
    for rsi_os, stop_mult, rr, st in results[:12]:
        print(f"{rsi_os:>4} {stop_mult:>5.1f} {rr:>4.1f} | {st['n']:>6} "
              f"{st['win_rate']:>5.1f}% {st['pf']:>6.2f} {st['exp']:>+7.2f}")

    print("\nCurrent live settings: "
          f"RSI={s.RSI_OVERSOLD}, stop={s.ATR_STOP_MULT}, RR={s.RR_TARGET}")
    print("\nPick a STABLE region (good neighbours), not the single top spike.")
    print("Small 60-day sample — treat as guidance, not gospel.")


if __name__ == "__main__":
    main()
