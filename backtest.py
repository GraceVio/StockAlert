"""
Backtester for the Stock Alert Bot strategy
-------------------------------------------
Replays the SAME dip-in-uptrend rules over recent historical 15-minute data and
reports statistics, so you can see how the strategy would have behaved BEFORE
trusting any settings.

For every historical signal it simulates the trade forward candle-by-candle:
  - if price hits the STOP first  -> loss of 1R
  - if price hits the TARGET first -> win of RR_TARGET R  (default +2R)
  - only one trade per ticker at a time (no overlapping positions)

It then prints: number of trades, win rate, average result, profit factor,
expectancy per trade (in R), and the worst losing streak.

IMPORTANT (honesty):
  * 15m Yahoo data is limited to ~60 days, so this is a SMALL, RECENT sample.
    Treat the numbers as a rough sanity check, not a promise.
  * Real trading is worse than any backtest (spread, the ~15-min data delay,
    slippage, and hesitation all cost money). A backtest is a ceiling.

Run:  python backtest.py
"""

import os
import pandas as pd
import yfinance as yf

import scanner as s  # reuse the exact indicators + parameters from the live bot

SEND_TELEGRAM = os.environ.get("BACKTEST_TELEGRAM", "1") == "1"

# Use the live watchlist and parameters so the backtest matches reality.
WATCHLIST     = s.WATCHLIST
INTERVAL      = s.INTERVAL
BACKTEST_DAYS = "60d"     # max Yahoo allows for 15m data
EMA_TREND     = s.EMA_TREND
RSI_OVERSOLD  = s.RSI_OVERSOLD
ATR_STOP_MULT = s.ATR_STOP_MULT
RR_TARGET     = s.RR_TARGET
MAX_HOLD_BARS = 100       # give up on a trade after this many candles (neither hit)


def simulate_ticker(ticker: str):
    """Return a list of trade results (each is +RR_TARGET, -1, or None=timeout)."""
    try:
        df = yf.download(ticker, period=BACKTEST_DAYS, interval=INTERVAL,
                         progress=False, auto_adjust=False)
    except Exception as e:
        print(f"  {ticker}: download error: {e}")
        return []

    if df is None or len(df) < EMA_TREND + 20:
        return []

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df["EMA"] = s.ema(df["Close"], EMA_TREND)
    df["RSI"] = s.rsi(df["Close"], s.RSI_LEN)
    df["ATR"] = s.atr(df, s.ATR_LEN)
    df = df.dropna()

    close = df["Close"].values
    high  = df["High"].values
    low   = df["Low"].values
    emav  = df["EMA"].values
    rsiv  = df["RSI"].values
    atrv  = df["ATR"].values

    trades = []
    i = 1
    n = len(df)
    while i < n - 1:
        in_uptrend   = close[i] > emav[i]
        was_oversold = rsiv[i - 1] <= RSI_OVERSOLD
        turning_up   = rsiv[i] > rsiv[i - 1]

        if in_uptrend and was_oversold and turning_up:
            entry  = close[i]
            stop   = entry - ATR_STOP_MULT * atrv[i]
            risk   = entry - stop
            target = entry + RR_TARGET * risk

            outcome = None
            for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, n)):
                if low[j] <= stop:          # stop hit first (conservative)
                    outcome = -1.0
                    i = j
                    break
                if high[j] >= target:       # target hit
                    outcome = RR_TARGET
                    i = j
                    break
            if outcome is not None:
                trades.append(outcome)
            else:
                i += MAX_HOLD_BARS          # timed out, move on
        i += 1
    return trades


def build_summary(all_trades) -> str:
    """Return the Telegram-formatted results text (or a 'no trades' note)."""
    n = len(all_trades)
    if n == 0:
        return "No trades generated in the sample. Try loosening RSI_OVERSOLD."

    wins   = [t for t in all_trades if t > 0]
    losses = [t for t in all_trades if t < 0]
    win_rate = len(wins) / n * 100
    gross_win  = sum(wins)
    gross_loss = -sum(losses)
    profit_factor = (gross_win / gross_loss) if gross_loss else float("inf")
    expectancy = sum(all_trades) / n           # average R per trade
    total_R = sum(all_trades)

    # worst losing streak
    streak = worst = 0
    for t in all_trades:
        streak = streak + 1 if t < 0 else 0
        worst = max(worst, streak)

    read = (
        f"each trade averaged {expectancy:+.2f}R "
        f"(~{expectancy:+.2f}% if you risk 1% per trade)"
        if expectancy > 0 else
        "negative expectancy on this sample — needs tuning before use"
    )

    return (
        "<b>📈 Backtest results</b> (recent ~60d, 15m)\n\n"
        f"Trades: <b>{n}</b>\n"
        f"Win rate: <b>{win_rate:.1f}%</b> ({len(wins)}W / {len(losses)}L)\n"
        f"Reward:risk 1:{RR_TARGET:.0f} (win +{RR_TARGET:.0f}R, loss -1R)\n"
        f"Profit factor: <b>{profit_factor:.2f}</b> (&gt;1 = profitable)\n"
        f"Expectancy: <b>{expectancy:+.2f}R</b> per trade\n"
        f"Total: {total_R:+.1f}R\n"
        f"Worst losing streak: {worst}\n\n"
        f"Read: {read}.\n"
        "<i>Small recent sample; live runs worse. Not financial advice.</i>"
    )


def run(send: bool = True) -> str:
    """Backtest the whole watchlist, return the summary text (and optionally send)."""
    all_trades = []
    for ticker in WATCHLIST:
        all_trades.extend(simulate_ticker(ticker))
    summary = build_summary(all_trades)
    if send:
        s.send_telegram(summary)
    return summary


def summarize(all_trades):
    summary = build_summary(all_trades)
    # console (ASCII-safe) + Telegram
    print("\n" + summary.replace("<b>", "").replace("</b>", "")
                        .replace("<i>", "").replace("</i>", "")
                        .replace("&gt;", ">"))
    if SEND_TELEGRAM:
        s.send_telegram(summary)


def main():
    print(f"Backtesting {len(WATCHLIST)} tickers on {INTERVAL} "
          f"over {BACKTEST_DAYS}...\n")
    all_trades = []
    for ticker in WATCHLIST:
        t = simulate_ticker(ticker)
        if t:
            wr = sum(1 for x in t if x > 0) / len(t) * 100
            print(f"  {ticker:9s} {len(t):3d} trades, {wr:4.0f}% win")
        all_trades.extend(t)
    summarize(all_trades)


if __name__ == "__main__":
    main()
