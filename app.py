"""
StockAlert Dashboard — everything on one page
----------------------------------------------
The Telegram bot answers one question per command. This shows all of it at once:
market mood, sector rotation, hot money, the baskets big money is trading, and
the dip-buy ranking — live, on your phone or laptop.

Runs the SAME code as the bot (scanner / market_mood / rank_today), so a number
here can never disagree with a number in Telegram.

RUN LOCALLY:   streamlit run app.py
DEPLOY FREE:   share.streamlit.io  → point it at this repo, main file app.py
               Add your keys under "Advanced settings → Secrets" (NOT in code):
                   ALPACA_KEY_ID = "..."
                   ALPACA_SECRET_KEY = "..."
                   FINNHUB_API_KEY = "..."
                   FRED_API_KEY = "..."
                   DASH_PASSWORD = "choose-something"      # optional gate
               The repo is PUBLIC, so anyone with the URL can open the app —
               set DASH_PASSWORD if you'd rather keep it to yourself. Your
               account size is never in the repo (account.txt is gitignored).
"""

import os
import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# Streamlit secrets -> environment, so scanner/alpaca/finnhub see them unchanged.
for _k in ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY", "FINNHUB_API_KEY",
           "FRED_API_KEY", "ACCOUNT_EUR", "TRADE_MODE"):
    try:
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = str(st.secrets[_k])
    except Exception:
        pass

import scanner as s                      # noqa: E402
import market_mood as mm                 # noqa: E402
import rank_today as rk                  # noqa: E402

st.set_page_config(page_title="StockAlert", page_icon="👑", layout="wide")

# ------------------------------------------------------------- text size
# Streamlit sizes almost everything in rem, so changing the ROOT font size
# scales the whole app — text, tables and all — in one go.
with st.sidebar:
    st.markdown("### ⚙️ Display")
    _size = st.slider("Text size", 10, 20, 13, 1,
                      help="Smaller = more rows fit on screen. 13 is compact, "
                           "16 is the Streamlit default.")
    _dense = st.checkbox("Compact spacing", value=True,
                         help="Trim padding so more fits on a phone screen.")

st.markdown(f"""<style>
  html {{ font-size: {_size}px; }}
  .block-container {{ padding-top: {'1rem' if _dense else '3rem'};
                      padding-bottom: 1rem;
                      max-width: 100%; }}
  {'div[data-testid="stVerticalBlock"] { gap: 0.4rem; }' if _dense else ''}
  [data-testid="stMetricValue"] {{ font-size: 1.5rem; }}
  [data-testid="stMetricLabel"] {{ font-size: 0.85rem; }}
  .stDataFrame {{ font-size: 0.9rem; }}
  .tg {{ font-size: 0.95rem; line-height: 1.45; }}
  .tg pre {{ font-size: 0.85rem; background: rgba(128,128,128,.12);
             padding: .6rem .8rem; border-radius: 6px; overflow-x: auto; }}
</style>""", unsafe_allow_html=True)

# ----------------------------------------------------------------- password
try:
    _pw = st.secrets.get("DASH_PASSWORD")
except Exception:
    _pw = None
if _pw:
    # Nothing below renders until the password matches — no data, no tickers.
    # The password itself lives in Streamlit Secrets, which is NOT part of the
    # public repo, so this is a real gate even though the code is public.
    if not st.session_state.get("ok"):
        st.title("👑 StockAlert")
        st.caption("Private dashboard — enter the password to continue.")
        entered = st.text_input("Password", type="password")
        if entered:
            if entered == _pw:
                st.session_state["ok"] = True
                st.rerun()
            else:
                st.error("Wrong password.")
        st.stop()


# -------------------------------------------------------------------- data
@st.cache_data(ttl=300, show_spinner="Fetching live market data…")
def get_snapshot():
    return mm.snapshot()


@st.cache_data(ttl=300, show_spinner="Scoring setups…")
def get_rank(n=15):
    rows = rk.rank(n)
    return [{k: v for k, v in r.items() if k != "parts"} for r in rows]


def pct(v, nd=1):
    return "—" if v is None else f"{v:+.{nd}f}%"


def colour(v):
    if v is None:
        return ""
    return "color:#16a34a;font-weight:600" if v > 0 else (
        "color:#dc2626;font-weight:600" if v < 0 else "")


def score_colour(v):
    """Green→amber→red band for the 0-100 score. Written by hand instead of
    pandas' background_gradient, which pulls in matplotlib — not installed on
    Streamlit Cloud, and not worth adding a plotting library just to tint a
    column."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    if v >= 65:
        return "background-color:#16a34a;color:white;font-weight:700"
    if v >= 55:
        return "background-color:#65a30d;color:white;font-weight:600"
    if v >= 45:
        return "background-color:#ca8a04;color:white"
    return "background-color:#6b7280;color:white"


# ------------------------------------------------------------------ header
now = dt.datetime.now(ZoneInfo("Europe/Berlin")).strftime("%d %b %Y, %H:%M CET")
c1, c2 = st.columns([4, 1])
c1.title("👑 StockAlert")
c1.caption(f"Live · {now}")
if c2.button("🔄 Refresh", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

snap = get_snapshot()
rows = snap["rows"]

if snap.get("session_note"):
    st.info(f"🕒 {snap['session_note']}")

# --------------------------------------------------------------- mood row
breadth = snap.get("breadth")
adv = sum(1 for r in rows if (r.get("day") or 0) > 0)
m1, m2, m3, m4 = st.columns(4)
if breadth is not None:
    mood = ("🟢 Risk-ON" if breadth >= 65 else "🙂 Mildly positive" if breadth >= 55
            else "😐 Mixed" if breadth >= 45 else "🔴 Risk-OFF")
    m1.metric("Market mood", mood, f"{breadth:.0f}% advancing")
for (nm, v), col in zip((snap.get("indices") or [])[:2], (m2, m3)):
    if v is not None:
        col.metric(nm, pct(v), delta=pct(v), delta_color="normal")
m4.metric("Stocks scanned", f"{len(rows)}", f"{adv} up / {len(rows)-adv} down")

st.divider()

tabs = st.tabs(["🌡️ Sectors", "🔥 Hot money", "🧲 Baskets",
                "🏆 Strongest", "👑 Dip ranking", "🔎 Stock"])

# ------------------------------------------------------------- 1. sectors
with tabs[0]:
    st.subheader("Where money is rotating today")
    sec = snap.get("sectors") or []
    if sec:
        sdf = pd.DataFrame(sec, columns=["Sector", "Today %"])
        strength = s.get_sector_strength()
        sdf["1 month %"] = [strength.get(n, {}).get("month") for n in sdf["Sector"]]
        sdf["Trend"] = ["🟢 up" if strength.get(n, {}).get("strong") else "🔴 weak"
                        for n in sdf["Sector"]]
        st.dataframe(
            sdf.style.map(colour, subset=["Today %", "1 month %"])
               .format({"Today %": "{:+.2f}%", "1 month %": "{:+.1f}%"}),
            use_container_width=True, hide_index=True)
        st.caption("🔥 top of this list = where money is flowing in RIGHT NOW. "
                   "Sector rotation drives most of a stock's daily move.")

# ----------------------------------------------------------- 2. hot money
with tabs[1]:
    st.subheader("Biggest movers — and how much money is behind them")
    hot = sorted([r for r in rows if r.get("day") is not None],
                 key=lambda r: r["day"], reverse=True)[:20]
    STATE = {"uptrend": "🪜 uptrend", "stalling": "⚠️ losing steam",
             "uptrend_broken": "🔻 uptrend broke", "downtrend": "❄️ downtrend",
             "basing": "〰️ basing", "sideways": "〰️ sideways"}
    hdf = pd.DataFrame([{
        "Ticker": r["ticker"], "Name": s.name_for(r["ticker"]) or "",
        "Today %": r["day"], "Money×": r.get("money"),
        "Off low %": r.get("recover"),
        "1wk %": (r.get("spans") or {}).get("1w"),
        "2wk %": (r.get("spans") or {}).get("2w"),
        "1mo %": (r.get("spans") or {}).get("1m"),
        "Trend": STATE.get((r.get("struct") or {}).get("state"), ""),
        "Sector": r.get("sector") or "",
    } for r in hot])
    st.dataframe(
        hdf.style.map(colour, subset=["Today %", "1wk %", "2wk %", "1mo %"])
           .format({"Today %": "{:+.1f}%", "Money×": "{:.1f}×",
                    "Off low %": "{:+.1f}%", "1wk %": "{:+.1f}%",
                    "2wk %": "{:+.1f}%", "1mo %": "{:+.1f}%"}, na_rep="—"),
        use_container_width=True, hide_index=True, height=560)
    st.caption("**Money×** = euros traded today vs this stock's own normal. Above "
               "~2× means real buyers, not drift. A big move on ~1× volume is not "
               "backed by institutions. Momentum needs a longer hold — see /mode wide.")

# -------------------------------------------------------------- 3. baskets
with tabs[2]:
    st.subheader("What big money is trading as one basket")
    st.caption("Found by measuring which stocks MOVE TOGETHER (4 weeks of daily "
               "returns) — not from any fixed sector list. A basket spanning "
               "several sectors means money is rotating by THEME.")
    groups = mm.flow_clusters(snap)
    if not groups:
        st.info("No clear baskets right now — today's moves look stock-specific "
                "rather than a coordinated rotation. (Quiet before the US open.)")
    for g in groups:
        head = "🟢 BUYING" if g["up"] else "🔴 SELLING"
        with st.container(border=True):
            a, b = st.columns([1, 3])
            a.metric(head, f"{g['avg']:+.1f}%", f"{g['money']:.1f}× money")
            b.write("**" + " · ".join(g["members"][:10]) + "**")
            spread = ", ".join(g["sectors"][:3])
            b.caption(f"{spread}" + (" — cuts across sectors"
                                     if len(g["sectors"]) > 1 else " (one sector)"))

# ------------------------------------------------------------ 4. strongest
with tabs[3]:
    st.subheader("Strongest over a chosen window")
    win = st.radio("Window", ["1d", "2d", "3d", "1w", "2w", "3w", "1m"],
                   index=3, horizontal=True)
    sel = [r for r in rows if (r.get("spans") or {}).get(win) is not None]
    sel.sort(key=lambda r: r["spans"][win], reverse=True)
    sdf2 = pd.DataFrame([{
        "Ticker": r["ticker"], "Name": s.name_for(r["ticker"]) or "",
        f"{win} %": r["spans"][win],
        "1d %": (r.get("spans") or {}).get("1d"),
        "1wk %": (r.get("spans") or {}).get("1w"),
        "1mo %": (r.get("spans") or {}).get("1m"),
        "Trend": STATE.get((r.get("struct") or {}).get("state"), ""),
        "Sector": r.get("sector") or "",
    } for r in sel[:20]])
    st.dataframe(
        sdf2.style.map(colour, subset=[f"{win} %", "1d %", "1wk %", "1mo %"])
            .format({f"{win} %": "{:+.1f}%", "1d %": "{:+.1f}%",
                     "1wk %": "{:+.1f}%", "1mo %": "{:+.1f}%"}, na_rep="—"),
        use_container_width=True, hide_index=True, height=560)

# ---------------------------------------------------------- 5. dip ranking
with tabs[4]:
    st.subheader("King Stocks — best dip-buy setups now")
    st.caption("The tested edge: deep oversold (RSI under 30) at a tested support "
               "level, with room to run before the next resistance.")
    rrows = get_rank(15)
    rdf = pd.DataFrame([{
        "Ticker": r["ticker"], "Name": s.name_for(r["ticker"]) or "",
        "Score": r["score"], "Price": r["price"], "RSI": r["rsi"],
        "Support": (r.get("support_tag") or "—"),
        "Room": (r.get("upside") or {}).get("room_r"),
        "In range %": r.get("range_pos"),
        "Sector": (r.get("sector") or {}).get("name", "") if isinstance(r.get("sector"), dict) else "",
    } for r in rrows])
    st.dataframe(
        rdf.style.map(score_colour, subset=["Score"])
           .format({"Price": "{:.2f}", "RSI": "{:.0f}", "Room": "{:.1f}R",
                    "In range %": "{:.0f}%"}, na_rep="—"),
        use_container_width=True, hide_index=True, height=560)
    st.caption("**Room** = how far to the next resistance, in units of your stop. "
               "Under 1R the target is blocked. **In range %** under 40 = a real "
               "pullback; over 55 = you'd be chasing.")

# -------------------------------------------------------------- 6. one stock
with tabs[5]:
    st.subheader("Full breakdown — same as /score in Telegram")
    sym = st.text_input("Ticker(s)", value="",
                        placeholder="NVDA   ·   or several: NVDA, SAP.DE, TTE.PA")
    quick = st.session_state.get("quick_sym")
    if quick and not sym:
        sym = quick
        st.session_state["quick_sym"] = None

    if sym:
        syms = [x.strip().upper() for x in sym.replace(";", ",").split(",")
                if x.strip()][:5]
        cols = st.columns(len(syms)) if len(syms) > 1 else [st]
        for col, one in zip(cols, syms):
            with col:
                with st.spinner(f"Analysing {one}…"):
                    try:
                        html = rk.score_one(one)
                    except Exception as e:
                        st.error(f"{one}: {e}")
                        continue
                # score_one already returns valid HTML (<b>/<i>/<pre>) — the very
                # same string Telegram renders — so show it as-is instead of
                # converting to markdown. Guarantees the dashboard and the bot
                # can never word things differently. Only newlines need bridging.
                st.markdown(f"<div class='tg'>{html.replace(chr(10), '<br>')}</div>",
                            unsafe_allow_html=True)
    else:
        st.caption("Type a ticker above, or jump straight from the ranking:")
        try:
            picks = [r["ticker"] for r in get_rank(15)][:8]
        except Exception:
            picks = []
        bcols = st.columns(min(4, len(picks)) or 1)
        for i, tk in enumerate(picks):
            if bcols[i % len(bcols)].button(tk, key=f"q{tk}",
                                            use_container_width=True):
                st.session_state["quick_sym"] = tk
                st.rerun()

st.divider()
st.caption("Scores rate ENTRY QUALITY right now — they are not price predictions. "
           "Position sizing and stops matter more to your P&L than picking #1 over #5.")
