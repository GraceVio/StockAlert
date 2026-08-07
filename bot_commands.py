"""
Telegram command handler — lets YOU type commands in the chat
-------------------------------------------------------------
Runs on a short GitHub Actions cron (every few minutes). Each run:
  1. asks Telegram for any new messages (getUpdates),
  2. processes commands ONLY from your own chat id (everyone else is ignored),
  3. replies, then acknowledges the updates so they aren't handled twice.

Because it acknowledges the offset back to Telegram at the end, no state file
is needed — Telegram itself holds the queue between runs.

SECURITY: it answers ONLY TELEGRAM_CHAT_ID. Any message from any other user is
logged and dropped. There is no way for a stranger to drive your bot.

Commands:
  /rank      → King Stocks: best-qualified setups right now (live)
  /scan      → run the dip-in-uptrend scan now and send any firing setups
  /sector    → sector strength ranking (the heatmap, as text)
  /help      → list commands

Run (locally, one pass):  python bot_commands.py
"""

import os
import requests
import scanner as s
import rank_today as rk

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
API     = f"https://api.telegram.org/bot{TOKEN}"

HELP = (
    "🤖 <b>Commands</b>\n"
    "/rank — 👑 King Stocks: best-qualified setups right now\n"
    "/scan — run the dip-in-uptrend scan now\n"
    "/sector — 📊 sector strength ranking\n"
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


def handle(cmd: str):
    cmd = cmd.lower().split("@")[0]  # strip @botname if present
    if cmd == "/rank":
        _reply("👑 Analysing the watchlist live… one moment.")
        rk.run(send=True)
        return None
    if cmd == "/scan":
        _reply("🔍 Scanning now… one moment.")
        return _do_scan()
    if cmd == "/sector":
        return _do_sector()
    if cmd in ("/help", "/start"):
        return HELP
    return None  # ignore non-commands silently


def main():
    if not TOKEN or not CHAT_ID:
        print("!! TELEGRAM_TOKEN / CHAT_ID not set.")
        return
    updates = _get_updates()
    if not updates:
        print("No new messages.")
        return

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
        reply = handle(text.split()[0])
        if reply:
            _reply(reply)

    # Acknowledge everything we just read so it isn't reprocessed next run.
    if last_id is not None:
        _get_updates(offset=last_id + 1)
    print("Done.")


if __name__ == "__main__":
    main()
