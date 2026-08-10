"""
Telegram command handler — lets YOU type commands in the chat
-------------------------------------------------------------
Two run modes:

  LOOP mode (default, used in the cloud):  python bot_commands.py
    Stays running and LONG-POLLS Telegram — replies the instant you type.
    Runs until POLL_SECONDS elapses (default ~5h40m), then exits so the next
    scheduled job takes over. This is what gives near-instant replies.

  ONCE mode (manual / fallback):           python bot_commands.py --once
    One pass: fetch pending messages, reply, acknowledge, exit.

Both process commands ONLY from your own chat id.

SECURITY: it answers ONLY TELEGRAM_CHAT_ID. Any message from any other user is
logged and dropped. There is no way for a stranger to drive your bot.

Commands: /rank /scan /sector /backtest /watchlist /add /remove /status /help
"""

import os
import sys
import time
import json
import subprocess
import requests
import datetime as dt
from zoneinfo import ZoneInfo
import yfinance as yf
import scanner as s
import rank_today as rk
import backtest as bt
import events as ev

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
API     = f"https://api.telegram.org/bot{TOKEN}"

HELP = (
    "🤖 <b>Commands</b>\n"
    "/rank — 👑 King Stocks: best-qualified setups right now\n"
    "/score SYM [SYM …] — 🔎 fit score (0-100), up to 5 stocks\n"
    "/scan — run the dip-in-uptrend scan now\n"
    "/sector — 📊 sector strength ranking\n"
    "/earnings — 📅 watchlist earnings, next 7 days\n"
    "/macro — 🏦 CPI/Fed/GDP events, next 7 days\n"
    "/backtest — 📈 how the strategy performed (~60d)\n"
    "/watchlist — 📋 show tracked tickers\n"
    "/add SYM — ➕ add a ticker (e.g. /add NVDA)\n"
    "/remove SYM — ➖ remove a ticker\n"
    "/status — 💚 is the bot alive &amp; market regime\n"
    "/help — this message\n\n"
    "<i>Nothing here predicts prices. All rules-based. You decide.</i>"
)


def _get_updates(offset=None):
    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"{API}/getUpdates", params=params, timeout=30)
        return r.json().get("result", []) if r.status_code == 200 else []
    except Exception as e:
        print(f"getUpdates failed: {e}")
        return []


def _reply(text):
    s.send_telegram(text)  # already targets TELEGRAM_CHAT_ID


def _pct(a, b):
    try:
        return (float(a) - float(b)) / float(b) * 100
    except Exception:
        return None


def _cell(v):
    return f"{v:+.1f}" if v is not None else "  -"


SECTOR_HOLDINGS = s.SECTOR_HOLDINGS   # shared list (defined in scanner.py)

# Short display labels for the sector buttons (callback carries the full name).
_SECTOR_SHORT = {
    "Technology": "Tech", "Comm. Services": "Comm", "Consumer Disc.": "Cons.Disc",
    "Consumer Staples": "Staples", "Energy": "Energy", "Financials": "Financials",
    "Health Care": "Health", "Industrials": "Industry", "Materials": "Materials",
    "Real Estate": "RealEst", "Utilities": "Utilities",
}


def _send_menu(text, markup):
    """Send a message with inline buttons."""
    try:
        requests.post(f"{API}/sendMessage", data={
            "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
            "reply_markup": json.dumps(markup),
        }, timeout=20)
    except Exception as e:
        print(f"menu send failed: {e}")


def _sector_rows():
    """Fetch sector ETF momentum (1h/2h/4h/24h) + 50-day strength, extended hours."""
    etfs = list(s.SECTOR_ETFS.items())
    tickers = [e for _, e in etfs]
    try:
        h = yf.download(tickers, period="7d", interval="60m", progress=False,
                        auto_adjust=False, prepost=True, group_by="ticker")
    except Exception:
        h = None
    monthly = s.get_sector_strength()
    rows = []
    for name, etf in etfs:
        tf = {}
        try:
            c = h[etf]["Close"].dropna()
            tf["1h"]  = _pct(c.iloc[-1], c.iloc[-2])  if len(c) >= 2  else None
            tf["2h"]  = _pct(c.iloc[-1], c.iloc[-3])  if len(c) >= 3  else None
            tf["4h"]  = _pct(c.iloc[-1], c.iloc[-5])  if len(c) >= 5  else None
            tf["24h"] = _pct(c.iloc[-1], c.iloc[-17]) if len(c) >= 17 else None
        except Exception:
            pass
        m = monthly.get(name, {})
        rows.append({"name": name, "tf": tf,
                     "strong": m.get("strong"), "month": m.get("month")})
    return rows


def _sector_table(only_tf=None):
    """Full 4-column table, or a single-timeframe ranked list if only_tf given."""
    rows = _sector_rows()
    if not any(r["tf"] for r in rows):
        return "Sector data unavailable right now — try again shortly."

    if only_tf:
        rows.sort(key=lambda r: (r["tf"].get(only_tf) is None,
                                 -(r["tf"].get(only_tf) or 0)))
        body = [f"{'Sector':<10}{only_tf:>7}  50d"]
        for r in rows:
            trend = "up" if r["strong"] else "dn"
            short = _SECTOR_SHORT.get(r["name"], r["name"])[:9]
            body.append(f"{short:<10}{_cell(r['tf'].get(only_tf)):>7}  {trend}")
        return (f"📊 <b>Sector momentum — {only_tf}</b>\n"
                "<i>%, live extended hours</i>\n"
                "<pre>" + "\n".join(body) + "</pre>\n"
                "<i>50d = above/below 50-day trend.</i>")

    rows.sort(key=lambda r: (r["tf"].get("24h") is None, -(r["tf"].get("24h") or 0)))
    body = [f"{'Sector':<10}{'1h':>6}{'4h':>6}{'24h':>6}  50d"]
    for r in rows:
        trend = "up" if r["strong"] else "dn"
        short = _SECTOR_SHORT.get(r["name"], r["name"])[:9]
        body.append(
            f"{short:<10}"
            f"{_cell(r['tf'].get('1h')):>6}"
            f"{_cell(r['tf'].get('4h')):>6}{_cell(r['tf'].get('24h')):>6}  {trend}"
        )
    return ("📊 <b>Sector momentum</b>\n"
            "<i>%, live extended hours</i>\n"
            "<pre>" + "\n".join(body) + "</pre>\n"
            "<i>Tap one timeframe to also see 2h. 50d = 50-day trend.</i>")


def _sector_stocks(name):
    """Top stocks in a sector, ranked by trade volume, with 24h trend."""
    tickers = SECTOR_HOLDINGS.get(name)
    if not tickers:
        return f"No stock list for {name}."
    try:
        d = yf.download(tickers, period="1mo", interval="1d", progress=False,
                        auto_adjust=False, group_by="ticker")
        h = yf.download(tickers, period="7d", interval="60m", progress=False,
                        auto_adjust=False, prepost=True, group_by="ticker")
    except Exception:
        return "Data unavailable right now — try again shortly."

    rows = []
    for t in tickers:
        try:
            dc = d[t]["Close"].dropna(); dv = d[t]["Volume"].dropna()
            last = float(dc.iloc[-1])
            ema50 = float(dc.ewm(span=50, adjust=False).mean().iloc[-1])
            up = last > ema50
            # Average daily volume in millions (stable liquidity measure — not
            # distorted by partial pre-market bars like a same-day ratio is).
            vol_m = float(dv.iloc[-21:-1].mean()) / 1e6
            hc = h[t]["Close"].dropna()
            ch24 = _pct(hc.iloc[-1], hc.iloc[-17]) if len(hc) >= 17 else _pct(dc.iloc[-1], dc.iloc[-2])
            price = float(hc.iloc[-1]) if len(hc) else last
        except Exception:
            continue
        rows.append({"t": t, "price": price, "ch24": ch24, "vol_m": vol_m, "up": up})

    if not rows:
        return f"No data for {name} stocks right now."
    rows.sort(key=lambda r: r["vol_m"], reverse=True)   # most-traded first

    lines = [f"📈 <b>{name} — most traded</b>",
             "<i>by avg daily volume · 24h = live extended hours</i>", ""]
    for i, r in enumerate(rows, 1):
        nm = s.name_for(r["t"])
        trend = "🟢 uptrend" if r["up"] else "🔴 downtrend"
        lines.append(
            f"{i}. <b>{r['t']}</b> · {nm}\n"
            f"   {r['price']:.2f} · 24h {_cell(r['ch24'])}% · "
            f"vol {r['vol_m']:.0f}M · {trend}"
        )
    lines.append("\n<i>Sorted by trade volume (liquidity). Trend = vs 50-day. "
                 "Context, not a prediction.</i>")
    return "\n".join(lines)


def _sector_menu_markup():
    tf_row = [{"text": t, "callback_data": f"sec_tf|{t}"}
              for t in ("1h", "2h", "4h", "24h")]
    overview = [{"text": "📊 All timeframes", "callback_data": "sec_tf|all"}]
    sector_btns = [{"text": _SECTOR_SHORT[n], "callback_data": f"sec_st|{n}"}
                   for n in SECTOR_HOLDINGS]
    sector_rows = [sector_btns[i:i + 3] for i in range(0, len(sector_btns), 3)]
    return {"inline_keyboard": [tf_row, overview] + sector_rows}


def _do_sector():
    """Send the interactive sector menu (buttons handled via callbacks)."""
    _send_menu(
        "📊 <b>Sector view</b>\n"
        "• Tap a <b>timeframe</b> for the momentum ranking\n"
        "• Tap a <b>sector</b> to see its stocks by volume &amp; trend",
        _sector_menu_markup(),
    )
    return None


def handle_callback(data):
    """React to an inline-button tap."""
    if data.startswith("sec_tf|"):
        tf = data.split("|", 1)[1]
        _reply(_sector_table(None if tf == "all" else tf))
    elif data.startswith("sec_st|"):
        name = data.split("|", 1)[1]
        _reply(_sector_stocks(name))


def _do_watchlist():
    wl = s.load_watchlist()
    picks = [t for t in wl if t not in ("SPY", "QQQ")]
    return (f"📋 <b>Watchlist</b> — {len(picks)} tickers (+ SPY/QQQ context)\n\n"
            + ", ".join(picks)
            + "\n\n<i>Add with /add SYM · remove with /remove SYM</i>")


def _valid_ticker(sym: str) -> bool:
    """Light check that Yahoo actually has data for this symbol."""
    try:
        df = yf.download(sym, period="5d", interval="1d",
                         progress=False, auto_adjust=False)
        return df is not None and len(df) > 0
    except Exception:
        return False


def _persist_watchlist():
    """Commit watchlist.txt back to the repo (only when running in Actions)."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    try:
        subprocess.run(["git", "add", "watchlist.txt"], check=False)
        subprocess.run(["git", "commit", "-m", "Update watchlist via Telegram"],
                       check=False)
        subprocess.run(["git", "push"], check=False)
    except Exception as e:
        print(f"watchlist persist failed: {e}")


def _do_add(sym: str):
    sym = sym.strip().upper()
    if not sym:
        return "Usage: /add SYM   (e.g. /add NVDA or /add SAP.DE)"
    wl = s.load_watchlist()
    if sym in wl:
        return f"ℹ️ <b>{sym}</b> is already on the watchlist."
    if not _valid_ticker(sym):
        return (f"❌ <b>{sym}</b> — no Yahoo data found. Check the symbol "
                f"(EU names need a suffix like .DE .AS .PA .L .F).")
    with open(s.WATCHLIST_FILE, "a", encoding="utf-8") as f:
        f.write(f"{sym}\n")
    _persist_watchlist()
    return (f"➕ Added <b>{sym}</b>. Watchlist now {len(wl)+1} tickers.\n"
            f"<i>Saved to GitHub — active from the next scan.</i>")


def _do_remove(sym: str):
    sym = sym.strip().upper()
    if not sym:
        return "Usage: /remove SYM   (e.g. /remove INTC)"
    if sym in ("SPY", "QQQ"):
        return "⚠️ SPY/QQQ are used as market context — kept on purpose."
    wl = s.load_watchlist()
    if sym not in wl:
        return f"ℹ️ <b>{sym}</b> isn't on the watchlist."
    with open(s.WATCHLIST_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    kept = [ln for ln in lines
            if ln.strip().upper() != sym or ln.strip().startswith("#")]
    with open(s.WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.writelines(kept)
    _persist_watchlist()
    return (f"➖ Removed <b>{sym}</b>. Watchlist now {len(wl)-1} tickers.\n"
            f"<i>Saved to GitHub — active from the next scan.</i>")


def _do_status():
    now = dt.datetime.now(ZoneInfo("Europe/Berlin")).strftime("%d %b %Y, %H:%M CET")
    wl = s.load_watchlist()
    picks = len([t for t in wl if t not in ("SPY", "QQQ")])
    try:
        healthy = s.market_is_healthy()
        regime = "🟢 HEALTHY (dip-buys active)" if healthy else "🔴 WEAK (dip-buys suppressed)"
    except Exception:
        regime = "unknown (data fetch failed)"
    return (f"💚 <b>Bot is alive</b>\n"
            f"🕒 {now}\n"
            f"📋 Watchlist: {picks} tickers\n"
            f"🌡️ Market regime: {regime}\n\n"
            f"<i>You're reading this, so the command loop works. Type /help for all commands.</i>")


def _do_scan():
    if not s.market_is_healthy():
        return ("Market regime <b>WEAK</b> (SPY below its 50-day average) — "
                "dip-buys are suppressed. No scan alerts this run.")
    hits = []
    for t in s.WATCHLIST:
        a = s.check_ticker(t)
        if a:
            hits.append(a)
    if not hits:
        return "🔍 Scan done — no setups firing right now."
    for a in hits:
        _reply(s.format_alert(a))
    return f"🔍 Scan done — {len(hits)} setup(s) sent above."


def handle(text: str):
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]  # strip @botname if present
    arg = parts[1] if len(parts) > 1 else ""
    if cmd == "/watchlist":
        return _do_watchlist()
    if cmd == "/add":
        return _do_add(arg)
    if cmd == "/remove":
        return _do_remove(arg)
    if cmd == "/status":
        return _do_status()
    if cmd == "/rank":
        _reply("👑 Analysing the watchlist live… one moment.")
        rk.run(send=True)
        return None
    if cmd == "/score":
        syms = [p.upper() for p in parts[1:] if p.strip()][:5]  # cap at 5
        if not syms:
            return "Usage: /score SYM [SYM …]   (e.g. /score NVDA TSLA AMD)"
        _reply(f"🔎 Scoring {', '.join(syms)}… one moment.")
        healthy = s.market_is_healthy()          # compute once for the batch
        for sym in syms:
            _reply(rk.score_one(sym, healthy=healthy))
        return None
    if cmd == "/scan":
        _reply("🔍 Scanning now… one moment.")
        return _do_scan()
    if cmd == "/sector":
        return _do_sector()
    if cmd == "/earnings":
        _reply("📅 Checking earnings dates… one moment.")
        return ev.earnings_text()
    if cmd == "/macro":
        return ev.macro_text()
    if cmd == "/backtest":
        _reply("📈 Backtesting the whole watchlist… this takes a minute.")
        bt.run(send=True)
        return None
    if cmd in ("/help", "/start"):
        return HELP
    return None  # ignore non-commands silently


def _process(updates):
    """Handle a batch of updates. Return the last update_id seen (or None)."""
    last_id = None
    for u in updates:
        last_id = u["update_id"]

        # Inline-button taps arrive as callback queries.
        cq = u.get("callback_query")
        if cq:
            try:
                requests.post(f"{API}/answerCallbackQuery",
                              data={"callback_query_id": cq["id"]}, timeout=10)
            except Exception:
                pass
            frm = str(cq.get("from", {}).get("id", ""))
            if frm != CHAT_ID:
                print(f"Ignored callback from foreign id {frm}")
                continue
            data = cq.get("data", "")
            print(f"Handling callback: {data!r}")
            handle_callback(data)
            continue

        msg = u.get("message") or u.get("edited_message")
        if not msg:
            continue
        frm = str(msg.get("chat", {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if frm != CHAT_ID:
            print(f"Ignored message from foreign chat id {frm}: {text!r}")
            continue
        if not text.startswith("/"):
            continue
        print(f"Handling command: {text!r}")
        reply = handle(text)
        if reply:
            _reply(reply)
    return last_id


def main():
    """ONCE mode: single pass, then acknowledge and exit."""
    if not TOKEN or not CHAT_ID:
        print("!! TELEGRAM_TOKEN / CHAT_ID not set.")
        return
    updates = _get_updates()
    if not updates:
        print("No new messages.")
        return
    last_id = _process(updates)
    if last_id is not None:
        _get_updates(offset=last_id + 1)  # acknowledge
    print("Done.")


def poll_loop():
    """LOOP mode: long-poll Telegram and reply instantly until the time budget ends."""
    if not TOKEN or not CHAT_ID:
        print("!! TELEGRAM_TOKEN / CHAT_ID not set.")
        return
    budget = int(os.environ.get("POLL_SECONDS", "20400"))  # ~5h40m default
    start = time.time()
    offset = None
    print(f"Long-poll loop starting (budget {budget}s). Waiting for commands…")
    while time.time() - start < budget:
        try:
            params = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(f"{API}/getUpdates", params=params, timeout=70)
            updates = r.json().get("result", []) if r.status_code == 200 else []
        except Exception as e:
            print(f"poll error: {e}")
            time.sleep(3)
            continue
        if updates:
            last_id = _process(updates)
            if last_id is not None:
                offset = last_id + 1
    print("Time budget reached — exiting so the next job takes over.")


if __name__ == "__main__":
    if "--once" in sys.argv or os.environ.get("RUN_MODE") == "once":
        main()
    else:
        poll_loop()
