"""
Alpaca real-time US quotes (free IEX feed)
------------------------------------------
Gives the CURRENT price for US stocks so /rank and /score reflect NOW instead of
Yahoo's ~15-min-delayed snapshot. Free: needs an Alpaca API key + secret set as
the ALPACA_KEY_ID / ALPACA_SECRET_KEY secrets. US symbols only (no exchange
suffix) — EU/UK names stay on yfinance. Falls back silently to yfinance if keys
are missing, the market is closed, or a symbol has no IEX trade.

Get free keys: https://alpaca.markets  → sign up → Paper account → API keys.
"""

import os
import requests

_LATEST = "https://data.alpaca.markets/v2/stocks/trades/latest"


def _keys():
    return os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")


def available() -> bool:
    k, s = _keys()
    return bool(k and s)


def _is_us(ticker: str) -> bool:
    # US symbols are plain (no exchange suffix like .DE/.AS/.PA/.L/.SW/.MI/.F).
    return "." not in ticker and ticker.isascii()


def latest_prices(tickers) -> dict:
    """{symbol: last_trade_price} for US symbols via the free IEX feed.
    Empty dict if keys are missing or on any error (caller falls back to yfinance)."""
    k, s = _keys()
    if not (k and s):
        return {}
    us = [t.upper() for t in tickers if _is_us(t)]
    if not us:
        return {}
    headers = {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}
    out = {}
    for i in range(0, len(us), 100):          # Alpaca allows many symbols/request
        chunk = us[i:i + 100]
        try:
            r = requests.get(_LATEST, params={"symbols": ",".join(chunk), "feed": "iex"},
                             headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            for sym, tr in (r.json().get("trades", {}) or {}).items():
                p = tr.get("p")
                if p and p > 0:
                    out[sym] = float(p)
        except Exception:
            continue
    return out
