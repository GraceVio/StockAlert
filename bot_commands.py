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
import subprocess
import requests
import datetime as dt
from zoneinfo import ZoneInfo
import yfinance as yf
import scanner as s
import rank_today as rk
import backtest as bt

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
API     = f"https://api.telegram.org/bot{TOKEN}"

HELP = (
    "🤖 <b>Commands</b>\n"
    "/rank — 👑 King Stocks: best-qualified setups right now\n"
    "/scan — run the dip-in-uptrend scan now\n"
    "/sector — 📊 sector strength ranking\n"
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


def _do_sector():
    """Text version of the sector heatmap."""
    data = s.get_sector_strength()
    if not data:
        return "Sector data unavailable right now — try again shortly."
    rows = sorted(data.items(), key=lambda kv: kv[1]["month"], reverse=True)
    lines = ["📊 <b>Sector strength</b> (1-month trend)", ""]
    for name, d in rows:
        mark = "🟢" if d["strong"] else "🔴"
        lines.append(f"{mark} {name}: {d['month']:+.1f}%  "
                     f"({'above' if d['strong'] else 'below'} 50-day)")
    lines.append("\n<i>Green = above its 50-day average. Context, not a prediction.</i>")
    return "\n".join(lines)


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
    if cmd == "/scan":
        _reply("🔍 Scanning now… one moment.")
        return _do_scan()
    if cmd == "/sector":
        return _do_sector()
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
