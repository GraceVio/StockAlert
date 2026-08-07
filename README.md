# Stock Alert Bot

Scans a watchlist of US + German/EU stocks every 15 minutes during market hours
and sends you a **Telegram alert** when a "buy-the-dip in an uptrend" setup appears.
Each alert includes an entry, a **stop-loss**, and a **target**.

It does **not** predict and does **not** place trades. It flags a defined setup
with a capped, known risk. You decide and click buy yourself. Not financial advice.

---

## What you get on your phone

> 🔔 **NVDA** long setup (15m)
> Buy-the-dip in uptrend · RSI turned up from 33 → 37
> Entry ~ 182.40
> Stop 179.10 (risk 3.30)
> Target 189.00 (2R)

---

## One-time setup (about 15 minutes)

### 1. Create your Telegram bot (2 min)
1. Install **Telegram** on your phone if you don't have it.
2. In Telegram, search for **@BotFather** and start a chat.
3. Send `/newbot`, pick a name and a username. BotFather gives you a **token**
   like `1234567890:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx`. Save it.
4. Search for **your new bot** by its username and send it any message (e.g. "hi").
   (This lets it message you.)

### 2. Get your chat ID (1 min)
1. In Telegram, search for **@userinfobot** and start it.
2. It replies with your **Id** (a number like `123456789`). Save it.

### 3. Put the code on GitHub (free cloud brain)
1. Create a free account at https://github.com if you don't have one.
2. Create a new **private** repository, e.g. `stock-alert-bot`.
3. Upload these files (drag-and-drop in the browser works):
   `scanner.py`, `requirements.txt`, and the `.github/workflows/scan.yml` file.

### 4. Add your secrets (so the bot can message you)
In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret.**
Add two secrets:
- `TELEGRAM_TOKEN` = the token from BotFather
- `TELEGRAM_CHAT_ID` = the Id from userinfobot

### 5. Turn it on and test
1. Go to the **Actions** tab in your repo, enable workflows if prompted.
2. Click **Stock Alert Scan → Run workflow** to test it immediately.
3. It will scan once now. If any stock is in a setup, you get a Telegram message.
   (Often there's nothing — the trigger is selective. That's normal.)

After that it runs automatically every 15 minutes on weekdays during market hours.

---

## Changing your watchlist or strategy
Open `scanner.py`:
- **Watchlist:** edit the `WATCHLIST` list at the top (US = plain ticker,
  German = `.DE`, Amsterdam = `.AS`).
- **Sensitivity:** `RSI_OVERSOLD` (higher = more alerts), `EMA_TREND`,
  `ATR_STOP_MULT` (stop distance), `RR_TARGET` (reward vs risk).

Commit the change on GitHub and the next scan uses it.

---

## Notes / honesty
- Free market data is delayed ~15 minutes. Fine for this kind of scanning,
  but alerts reflect prices from a few minutes ago.
- This is a starting strategy. We should watch its alerts for a couple of weeks
  and tune before trusting it with real size.
