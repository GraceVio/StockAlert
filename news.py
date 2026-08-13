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


# Generic corporate words that don't identify a specific company (so we don't
# match "Group"/"Holdings"/"Inc" against unrelated stories).
_GENERIC = {
    "inc", "corp", "co", "ltd", "plc", "the", "com", "group", "holdings",
    "holding", "company", "companies", "international", "technologies",
    "technology", "systems", "and", "industries", "enterprises", "sa", "se",
    "nv", "ag", "spa", "class", "adr", "ordinary", "shares", "stock",
}


def _match_terms(ticker, name):
    """Identifying terms for a company: its ticker base + meaningful name words.
    Used to keep only news that actually mentions THIS company."""
    terms = set()
    base = (ticker or "").split(".")[0].lower()
    if len(base) >= 2:
        terms.add(base)
    if name:
        low = name.lower()
        terms.add(low)                              # full name, e.g. "jd.com"
        for w in re.split(r"[^a-z0-9]+", low):
            if len(w) >= 4 and w not in _GENERIC:
                terms.add(w)
    return terms


def _is_relevant(title, summary, terms):
    """True if the headline/summary actually names the company. Short tickers are
    matched as whole words (so 'JD' doesn't match 'adjust'); longer names by
    substring."""
    blob = " " + (str(title or "") + " " + str(summary or "")).lower() + " "
    for term in terms:
        if len(term) <= 3:
            if re.search(r"\b" + re.escape(term) + r"\b", blob):
                return True
        elif term in blob:
            return True
    return False


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


def _raw_news(ticker, limit=6, relevant_to=None):
    """Pull headlines for a ticker from yfinance (handles both news formats),
    returning (title, publisher, summary) tuples.

    If `relevant_to=(symbol, name)` is given, keep ONLY stories that actually
    name that company (title or summary) — yfinance's per-ticker feed mixes in
    loosely-related sector/peer stories, which aren't useful for a single stock."""
    try:
        items = s.yf.Ticker(ticker).news or []
    except Exception:
        return []
    terms = _match_terms(*relevant_to) if relevant_to else None
    out = []
    for n in items:
        c = n.get("content", {}) or {}
        title = c.get("title") or n.get("title")
        if not title:
            continue
        summ = c.get("summary") or c.get("description") or n.get("summary") or ""
        pub = (c.get("provider", {}) or {}).get("displayName") or n.get("publisher") or ""
        if terms is not None and not _is_relevant(title, summ, terms):
            continue
        out.append((title, pub, summ))
        if len(out) >= limit:
            break
    return out


def _company_headlines(ticker, limit=6):
    """Company-specific headlines as (title, source, summary) tuples.
    Prefers Finnhub /company-news (genuinely per-company, US/NA + a free key);
    falls back to yfinance filtered to stories that actually name the company."""
    name = s.name_for(ticker)
    try:
        import finnhub_data as fh
        fn = fh.company_news(ticker, days=10, limit=limit)
        if fn:
            return [(x["headline"], x["source"], x["summary"]) for x in fn]
    except Exception:
        pass
    return _raw_news(ticker, limit=limit, relevant_to=(ticker, name))


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
    raw = _company_headlines(ticker, limit=6)
    if not raw:
        return (f"📰 <b>{title} — news</b>\n\n"
                f"No recent headlines that directly mention this company.")
    classed = [(classify(t)[0], classify(t)[1], t, pub) for t, pub, _ in raw]
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
        for t, pub, _ in _raw_news(etf, limit=6):
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
    """Compact lean for embedding elsewhere (e.g. /rank). Returns (emoji, word)
    or (None, None) if no news."""
    raw = _company_headlines(ticker, limit=5)
    if not raw:
        return None, None
    leans = [(classify(t)[0], classify(t)[1]) for t, _, _ in raw]
    lean, p, n = _lean_from(leans)
    word = {"pos": "positive", "neg": "negative", "neu": "mixed"}[lean]
    return _emoji(lean), word


def stock_news_brief(ticker, n=2):
    """A short news block for /score: overall lean + the 1-2 most meaningful
    headlines (positive OR negative — neutral ones are skipped so the reader
    sees what could actually MOVE the stock). Empty string if no news.

    HTML-formatted, ready to drop into score_one. Both directions matter here:
    a 🔴 headline is a reason to hold off (falling-knife risk), a 🟢 one confirms
    the uptrend thesis — but note a fresh big-positive story often means the pop
    already happened, so it's context, not a green light to chase."""
    raw = _company_headlines(ticker, limit=6)
    if not raw:
        return ""
    classed = [(classify(t)[0], classify(t)[1], t, pub) for t, pub, _ in raw]
    lean, p, nneg = _lean_from([(l, r) for l, r, _, _ in classed])
    head = {"pos": "🟢 leaning positive", "neg": "🔴 leaning negative",
            "neu": "⚪ mixed / neutral"}[lean]
    # Show the headlines that carry a signal first (pos/neg before neutral).
    ranked = sorted(classed, key=lambda c: 0 if c[0] in ("pos", "neg") else 1)
    picks = ranked[:n]
    lines = [f"\n📰 <b>News: {head}</b>  <i>({p} pos · {nneg} neg)</i>"]
    for l, r, t, pub in picks:
        tag = _emoji(l) + ("⚠️" if r else "")
        src = f" <i>— {pub}</i>" if pub else ""
        lines.append(f"   {tag} {t[:120]}{src}")
    lines.append("   <i>Rough headline read — direction ≠ next price move. "
                 "/news " + ticker.upper() + " for more.</i>")
    return "\n".join(lines)
