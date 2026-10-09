"""
Live track record — does a high score actually make money, NOW?
---------------------------------------------------------------
Backtests only describe the past. This logs the Dip ranking every trading day
after the US close, then checks each pick 5 and 10 trading days later against
the S&P 500 over the same days, so the score proves itself (or not) on real,
current trades.

  * Entry = the stock's OFFICIAL daily close on the pick day (reproducible; the
    live ranking may have used an after-hours price).
  * Results come from daily closes only; "fell first" = the lowest low in the
    5 days after entry.
  * A stock that stays in the ranking for several days counts ONCE per 7 days
    (its first appearance), so one long dip cannot inflate the numbers.

Run daily by .github/workflows/track.yml:  python track_record.py
The CSV lives in the public repo; it holds no account data.
"""

import os
import datetime as dt
import numpy as np
import pandas as pd
import yfinance as yf

FILE = "track_record.csv"
COLS = ["date", "ticker", "score", "entry", "rsi", "room", "fall", "trend",
        "earn_block", "mode", "r5", "r10", "spy5", "spy10", "fell_first"]
MIN_SCORE = 45            # log the 45+ rows so the bands can be compared
REPO = os.environ.get("GITHUB_REPO", "GraceVio/StockAlert")


# ------------------------------------------------------------------ storage
def load(prefer_remote: bool = False) -> pd.DataFrame:
    """The record as a DataFrame. The website reads the copy on GitHub (it is
    updated daily by the workflow); everything else reads the local file."""
    srcs = []
    if prefer_remote:
        srcs.append(f"https://raw.githubusercontent.com/{REPO}/main/{FILE}")
    srcs.append(FILE)
    for src in srcs:
        try:
            df = pd.read_csv(src)
            if len(df.columns):
                return df.reindex(columns=COLS)
        except Exception:
            continue
    return pd.DataFrame(columns=COLS)


def save(df: pd.DataFrame):
    df.sort_values(["date", "score"], ascending=[True, False]).to_csv(
        FILE, index=False, float_format="%.3f")


# ------------------------------------------------------------------ logging
def log_today(df: pd.DataFrame) -> pd.DataFrame:
    """Append today's Dip ranking (score 45+)."""
    import rank_today as rk
    import scanner as s
    rows = rk.rank(15)
    rows = [r for r in rows if r["score"] >= MIN_SCORE]
    if not rows:
        return df
    tick = [r["ticker"] for r in rows]
    closes = yf.download(tick, period="10d", interval="1d", progress=False,
                         auto_adjust=False, group_by="ticker", threads=True)
    mode = s.load_mode()
    new = []
    for r in rows:
        t = r["ticker"]
        try:
            c = (closes[t] if len(tick) > 1 else closes)["Close"].dropna()
            day, entry = c.index[-1].date().isoformat(), float(c.iloc[-1])
        except Exception:
            continue
        if ((df["date"] == day) & (df["ticker"] == t)).any():
            continue                       # holiday re-run: same close, skip
        new.append({"date": day, "ticker": t, "score": r["score"],
                    "entry": entry, "rsi": round(r["rsi"], 1),
                    "room": round(((r.get("upside") or {}).get("room_r") or 0), 2),
                    "fall": rk.fall_label(r), "trend": rk.trend_label(r),
                    "earn_block": bool(r.get("earn_block")), "mode": mode})
    if new:
        df = pd.concat([df, pd.DataFrame(new)], ignore_index=True)
    return df


# --------------------------------------------------------------- evaluating
def evaluate(df: pd.DataFrame) -> pd.DataFrame:
    """Fill r5 / r10 / spy5 / spy10 / fell_first wherever enough days passed."""
    todo = df[df["r10"].isna()]
    if todo.empty:
        return df
    tick = sorted(set(todo["ticker"])) + ["SPY"]
    start = (pd.to_datetime(todo["date"]).min() - pd.Timedelta(days=5)).date()
    data = yf.download(tick, start=start.isoformat(), interval="1d",
                       progress=False, auto_adjust=False, group_by="ticker",
                       threads=True)

    def series(t):
        d = data[t].dropna(subset=["Close"])
        d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
        return d

    spy = series("SPY")["Close"]
    for i, row in todo.iterrows():
        try:
            d = series(row["ticker"])
            day = pd.Timestamp(row["date"])
            pos = d.index.searchsorted(day)
            if pos >= len(d) or d.index[pos] != day:
                continue
            c, lo, e = d["Close"].values, d["Low"].values, float(row["entry"])
            sp = spy.index.searchsorted(day)
            for k, col, scol in ((5, "r5", "spy5"), (10, "r10", "spy10")):
                if pos + k < len(c) and pd.isna(df.at[i, col]):
                    df.at[i, col] = (c[pos + k] / e - 1) * 100
                    if sp + k < len(spy):
                        df.at[i, scol] = (spy.iloc[sp + k] / spy.iloc[sp] - 1) * 100
            if pos + 5 < len(lo) and pd.isna(df.at[i, "fell_first"]):
                df.at[i, "fell_first"] = (lo[pos + 1:pos + 6].min() / e - 1) * 100
        except Exception:
            continue
    return df


# -------------------------------------------------------------------- stats
def first_picks(df: pd.DataFrame) -> pd.DataFrame:
    """Keep a ticker's first appearance per 7 days (one dip = one trade)."""
    df = df.copy()
    df["d"] = pd.to_datetime(df["date"])
    keep, last = [], {}
    for i, r in df.sort_values("d").iterrows():
        prev = last.get(r["ticker"])
        if prev is None or (r["d"] - prev).days > 7:
            keep.append(i)
            last[r["ticker"]] = r["d"]
    return df.loc[keep]


BANDS = (("75+", 75, 101), ("60-74", 60, 75), ("45-59", 45, 60))


def stats(df: pd.DataFrame, days: int = 5):
    """Per score band: n, % up, avg %, avg vs S&P 500, avg fell-first."""
    col, scol = f"r{days}", f"spy{days}"
    p = first_picks(df)
    p = p[p[col].notna()]
    out = []
    for lab, lo, hi in BANDS:
        x = p[(p["score"] >= lo) & (p["score"] < hi)]
        if x.empty:
            out.append({"band": lab, "n": 0})
            continue
        out.append({"band": lab, "n": len(x),
                    "up": (x[col] > 0).mean() * 100,
                    "avg": x[col].mean(),
                    "vs_spy": (x[col] - x[scol]).mean(),
                    "fell_first": x["fell_first"].mean()})
    return out


def since(df: pd.DataFrame) -> str:
    return str(df["date"].min()) if len(df) else "—"


# ----------------------------------------------------------------- telegram
def record_text() -> str:
    df = load()
    if df.empty:
        return ("📊 <b>Track record</b>\n\nNo picks logged yet — the first ones "
                "are recorded after tonight's US close.")
    lines = [f"📊 <b>Track record</b> — live picks since {since(df)}", ""]
    for days in (5, 10):
        st = stats(df, days)
        if not any(r["n"] for r in st):
            lines.append(f"<i>After {days} days: no results yet (needs {days} "
                         "trading days).</i>")
            continue
        lines.append(f"<b>After {days} trading days</b>")
        for r in st:
            if r["n"]:
                lines.append(f"  {r['band']:>5}: {r['n']:3d} picks · {r['up']:.0f}% up · "
                             f"avg {r['avg']:+.1f}% · vs S&amp;P {r['vs_spy']:+.1f}%")
        lines.append("")
    lines.append("<i>Each dip counts once (first day in the ranking), bought at "
                 "that day's close.</i>")
    return "\n".join(lines)


# --------------------------------------------------------------------- main
def main():
    df = load()
    df = evaluate(df)
    df = log_today(df)
    save(df)
    done = df["r5"].notna().sum()
    print(f"track_record: {len(df)} rows · {done} with 5-day results")


if __name__ == "__main__":
    main()
