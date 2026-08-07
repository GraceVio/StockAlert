"""
Sector Strength Report
----------------------
Replaces the TradingView heatmap with hard numbers. For each US sector (via its
SPDR sector ETF) it measures:
  - today's % change          (who is being bought/sold right now)
  - 1-month % return          (short-term trend)
  - above/below 50-day average (in an uptrend or not)

It ranks sectors strongest -> weakest and sends a Telegram summary, so you can
see at a glance which areas are strong before taking a trade in that area.

Run on its own (e.g. once each morning) or manually.  Reuses Telegram from scanner.
"""

import pandas as pd
import yfinance as yf
import scanner as s   # reuse send_telegram

SECTOR_ETFS = {
    "Technology":            "XLK",
    "Communication Svcs":    "XLC",
    "Consumer Discretionary":"XLY",
    "Consumer Staples":      "XLP",
    "Energy":                "XLE",
    "Financials":            "XLF",
    "Health Care":           "XLV",
    "Industrials":           "XLI",
    "Materials":             "XLB",
    "Real Estate":           "XLRE",
    "Utilities":             "XLU",
}


def sector_stats(etf: str):
    try:
        df = yf.download(etf, period="4mo", interval="1d",
                         progress=False, auto_adjust=False)
    except Exception:
        return None
    if df is None or len(df) < 55:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    close = df["Close"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    month_ago = float(close.iloc[-21]) if len(close) >= 21 else float(close.iloc[0])
    sma50 = float(close.rolling(50).mean().iloc[-1])
    return {
        "today_pct": (last - prev) / prev * 100,
        "month_pct": (last - month_ago) / month_ago * 100,
        "above_50d": last > sma50,
    }


def build_report():
    rows = []
    for name, etf in SECTOR_ETFS.items():
        st = sector_stats(etf)
        if st:
            rows.append((name, st))
    # rank by 1-month trend (strength), tie-broken by today's move
    rows.sort(key=lambda r: (r[1]["month_pct"], r[1]["today_pct"]), reverse=True)

    lines = ["<b>📊 Sector strength</b> (strongest → weakest)\n"]
    for name, st in rows:
        trend = "🟢 uptrend" if st["above_50d"] else "🔴 below 50d"
        lines.append(
            f"{name}: today {st['today_pct']:+.1f}% · "
            f"1mo {st['month_pct']:+.1f}% · {trend}"
        )
    if rows:
        strong = [n for n, st in rows if st["above_50d"] and st["month_pct"] > 0]
        if strong:
            lines.append("\n<b>Strong sectors right now:</b> " + ", ".join(strong[:3]))
    lines.append("\n<i>Overview only. Not financial advice.</i>")
    return "\n".join(lines)


def main():
    report = build_report()
    print(report)
    s.send_telegram(report)


if __name__ == "__main__":
    main()
