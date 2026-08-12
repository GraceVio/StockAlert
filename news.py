"""
News + sentiment — a rough positive/negative read of headlines
--------------------------------------------------------------
Two feeds:

  • STOCK news   — headlines for one ticker, each tagged 🟢 positive / 🔴 negative
                   / ⚪ neutral, plus an overall lean.
  • MARKET news  — broad market headlines (via the index ETFs) with a lean, and a
                   ⚠️ flag if risk-off themes appear (war, tariffs, shutdown, …).

HONEST LIMITS: this is a keyword-based read of headline text — not real sentiment
analysis and not a prediction. A headline can be sarcastic, priced-in, or wrong.
Treat it as a quick "which way is the news leaning" glance, then read the actual
stories before acting. Direction of a headline does not tell you the next price move.

Source: yfinance `.news` (free). No API key.
"""

import re
import scanner as s

# Finance-aware keyword lexicons (matched on whole words, case-insensitive).
_POS = {
    "beat", "beats", "tops", "topped", "surge", "surges", "surged", "jump",
    "jumps", "jumped", "soar", "soars", "rally", "rallies", "upgrade",
    "upgraded", "upgrades", "raises", "raised", "boost", "boosts", "record",
    "strong", "gains", "gain", "wins", "win", "approval", "approved",
    "outperform", "higher", "rises", "rise", "positive", "profit", "growth",
    "expands", "expansion", "buyback", "dividend", "breakthrough", "wins",
    "bullish", "optimistic", "rebound", "rebounds", "climbs", "climb", "leap",
}
_NEG = {
    "miss", "misses", "missed", "falls", "fall", "fell", "drop", "drops",
    "dropped", "plunge", "plunges", "plunged", "sink", "sinks", "slump",
    "slumps", "downgrade", "downgraded", "downgrades", "cuts", "cut", "lawsuit",
    "probe", "investigation", "recall", "recalls", "warning", "warns", "weak",
    "loss", "losses", "layoffs", "slashes", "slash", "halts", "halt", "delay",
    "delays", "fraud", "decline", "declines", "lower", "negative", "bearish",
    "selloff", "sell-off", "tumble", "tumbles", "crash", "crashes", "slips",
    "slip", "sued", "fine", "fined", "bankruptcy", "default", "downturn",
}
# Broad risk-off / geopolitical themes that can shake the whole market.
_RISK = {
    "war", "invasion", "invade", "conflict", "missile", "attack", "attacks",
    "strike", "sanction", "sanctions", "tariff", "tariffs", "shutdown",
    "default", "recession", "crisis", "geopolitical", "escalation", "nuclear",
    "pandemic", "outbreak", "oil spike", "trade war", "embargo", "coup",
}


def _words(text):
    return set(re.findall(r"[a-z][a-z\-']+", (text or "").lower()))


def classify(title):
    """Return ('pos'|'neg'|'neu', has_risk_flag) for a headline."""
    w = _words(title)
    pos = len(w & _POS)
    neg = len(w & _NEG)
    risk = bool(w & _RISK) or any(k in (title or "").lower() for k in _RISK if " " in k)
    lean = "pos" if pos > neg else ("neg" if neg > pos else "neu")
    return lean, risk


def _emoji(lean):
    return {"pos": "🟢", "neg": "🔴", "neu": "⚪"}.get(lean, "⚪")


def _raw_news(ticker, limit=6):
    """Pull headlines for a ticker from yfinance (handles both news formats)."""
    try:
        items = s.yf.Ticker(ticker).news or []
    except Exception:
        return []
    out = []
    for n in items[:limit]:
        c = n.get("content", {}) or {}
        title = c.get("title") or n.get("title")
        pub = (c.get("provider", {}) or {}).get("displayName") or n.get("publisher") or ""
        if title:
            out.append((title, pub))
    return out


def _lean_from(headlines):
    """Overall lean from a list of (lean, risk) classifications."""
    p = sum(1 for l, _ in headlines if l == "pos")
    n = sum(1 for l, _ in headlines if l == "neg")
    if p > n:
        return "pos", p, n
    if n > p:
        return "neg", p, n
    return "neu", p, n


def stock_news_text(ticker):
    """Headlines + overall lean for one ticker."""
    ticker = ticker.strip().upper()
    nm = s.name_for(ticker)
    title = ticker + (f" · {nm}" if nm else "")
    raw = _raw_news(ticker)
    if not raw:
        return (f"📰 <b>{title} — news</b>\n\n"
                f"No recent headlines found for this symbol.")
    classed = [(classify(t)[0], classify(t)[1], t, pub) for t, pub in raw]
    lean, p, n = _lean_from([(l, r) for l, r, _, _ in classed])
    head = {"pos": "🟢 leaning positive", "neg": "🔴 leaning negative",
            "neu": "⚪ mixed / neutral"}[lean]

    lines = [f"📰 <b>{title} — news</b>",
             f"Overall: <b>{head}</b>  ({p} positive · {n} negative)", ""]
    for l, r, t, pub in classed:
        tag = _emoji(l) + ("⚠️" if r else "")
        src = f"  <i>— {pub}</i>" if pub else ""
        lines.append(f"{tag} {t[:110]}{src}")
    lines.append("\n<i>Rough automated read of headline wording — check the "
                 "stories before acting. News direction ≠ next price move.</i>")
    return "\n".join(lines)


def market_news_text():
    """Broad market headlines with a lean + a risk-off flag."""
    seen, raw = set(), []
    for etf in ("SPY", "QQQ", "^GSPC", "^DJI"):
        for t, pub in _raw_news(etf, limit=6):
            key = t[:60]
            if key not in seen:
                seen.add(key)
                raw.append((t, pub))
    if not raw:
        return ("🌍 <b>Market news</b>\n\nNo broad market headlines found right now."
                " Try again shortly.")
    classed = [(classify(t)[0], classify(t)[1], t, pub) for t, pub in raw[:8]]
    lean, p, n = _lean_from([(l, r) for l, r, _, _ in classed])
    risk_hits = [t for l, r, t, _ in classed if r]

    head = {"pos": "🟢 leaning positive", "neg": "🔴 leaning negative",
            "neu": "⚪ mixed / neutral"}[lean]
    lines = ["🌍 <b>Market news — big picture</b>",
             f"Overall mood: <b>{head}</b>  ({p} positive · {n} negative)", ""]
    if risk_hits:
        lines.append("🔴 <b>Risk-off themes detected</b> (war/tariffs/shutdown-type "
                     "news) — the whole market can wobble. Trade smaller / wait.")
        lines.append("")
    for l, r, t, pub in classed:
        tag = _emoji(l) + ("⚠️" if r else "")
        src = f"  <i>— {pub}</i>" if pub else ""
        lines.append(f"{tag} {t[:110]}{src}")
    lines.append("\n<i>Rough automated read of headline wording — a quick mood "
                 "glance, not a forecast. Read the stories before acting.</i>")
    return "\n".join(lines)


def stock_lean(ticker):
    """Compact lean for embedding elsewhere (e.g. /score). Returns (emoji, word)
    or (None, None) if no news."""
    raw = _raw_news(ticker, limit=5)
    if not raw:
        return None, None
    leans = [(classify(t)[0], classify(t)[1]) for t, _ in raw]
    lean, p, n = _lean_from(leans)
    word = {"pos": "positive", "neg": "negative", "neu": "mixed"}[lean]
    return _emoji(lean), word
