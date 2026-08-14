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
    _size = st.slider("Text size", 10, 20, 15, 1,
                      help="Smaller = more rows fit on screen. 16 is the "
                           "Streamlit default.")
    _dense = st.checkbox("Compact spacing", value=True,
                         help="Trim padding so more fits on a phone screen.")

st.markdown(f"""<style>
  html {{ font-size: {_size}px; }}
  /* Generous top padding: Streamlit's floating toolbar was clipping the title. */
  .block-container {{ padding-top: 3.2rem; padding-bottom: 1rem;
                      padding-left: .8rem; padding-right: .8rem;
                      max-width: 100%; }}
  {'div[data-testid="stVerticalBlock"] { gap: 0.35rem; }' if _dense else ''}
  h1 {{ font-size: 1.75rem !important; margin-bottom: 0 !important; }}
  h2, h3 {{ font-size: 1.35rem !important; }}
  /* Header stats as one wrapping row of chips instead of tall metric blocks. */
  .hdr {{ display: flex; flex-wrap: wrap; gap: .4rem .5rem; margin: .1rem 0 .5rem; }}
  .chip {{ display: flex; flex-direction: column; padding: .3rem .6rem;
           border: 1px solid rgba(128,128,128,.28); border-radius: 8px;
           min-width: 6.5rem; }}
  .chip .lbl {{ font-size: .78rem; opacity: .65; line-height: 1.1; }}
  .chip .val {{ font-size: 1.15rem; font-weight: 600; line-height: 1.25; }}
  .stDataFrame, .stDataFrame td, .stDataFrame th {{ font-size: 1.05rem; }}
  .tg {{ font-size: 1.1rem; line-height: 1.5; }}
  .tg pre {{ font-size: 1.0rem; background: rgba(128,128,128,.12);
             padding: .6rem .8rem; border-radius: 6px; overflow-x: auto; }}
  .stTabs [data-baseweb="tab"] {{ padding: .35rem .6rem; font-size: 1.05rem; }}
  /* Grey helper text stays SMALL — it's context, not something to emphasise. */
  [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p,
  .stCaption, small {{ font-size: 0.82rem !important; opacity: .72; }}
  /* Title row must stay side-by-side on a phone (Streamlit stacks columns on
     narrow screens by default, which pushed Refresh onto its own line). */
  .hdrow div[data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap !important; }}
  .hdrow div[data-testid="column"]:last-child {{ flex: 0 0 auto !important;
      width: auto !important; min-width: 0 !important;
      display: flex; align-items: flex-start; justify-content: flex-end; }}
  .hdrow .stButton > button {{ height: 2.6rem; width: 2.6rem; padding: 0;
      font-size: 1.2rem; border-radius: 10px; }}
  /* Compact session banner — the colour already does the shouting. */
  .sess {{ padding: .35rem .7rem; border-radius: 8px; font-size: .9rem;
           background: rgba(56,139,253,.14); border: 1px solid rgba(56,139,253,.35);
           margin: .1rem 0 .9rem; display: inline-block; }}
  /* Breathing room around the Sectors/Baskets pills. */
  div[data-testid="stButtonGroup"] {{ margin: .55rem 0 .7rem; }}
  /* ---- Basket cards -------------------------------------------------
     Spacing rules: generous OUTSIDE the card and between its three blocks,
     TIGHT between the wrapped ticker lines. Each ticker+name is one atom that
     never splits across a line break. */
  .card {{ border: 1px solid rgba(128,128,128,.28); border-radius: 12px;
           padding: .8rem .9rem .85rem; margin: 0 0 .9rem 0; }}
  .card .crow {{ display: flex; flex-wrap: wrap; align-items: baseline;
                 gap: .5rem; margin-bottom: .6rem; }}
  .card .tag {{ font-size: .88rem; font-weight: 700; letter-spacing: .04em; }}
  .card .big {{ font-size: 1.3rem; font-weight: 700; line-height: 1; }}
  .card .sub {{ font-size: .86rem; opacity: .7; }}
  .card .names {{ display: flex; flex-wrap: wrap; gap: .2rem .9rem;
                  font-size: .95rem; line-height: 1.35; margin-bottom: .6rem; }}
  .card .pair {{ white-space: nowrap; }}
  .card .nm {{ opacity: .65; }}
  .card .foot {{ font-size: .8rem; opacity: .6;
                 padding-top: .5rem;
                 border-top: 1px solid rgba(128,128,128,.16); }}
  /* Sector drill-down */
  .sechd {{ font-size: 1.05rem; font-weight: 700; margin: .2rem 0 .4rem; }}
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


def show_table(df, numeric, index_col="Ticker", height=None, extra=None,
               fmt=None, select_key=None):
    """One table style for the whole app.

    * Ticker becomes the INDEX — Streamlit keeps the index column PINNED while
      you scroll sideways, so you never lose track of which stock a row is.
    * Every numeric column is ROUNDED before display. The styler only changes
      how a value LOOKS; the underlying float stays full precision and Streamlit
      shows it raw when you tap a cell (that "-17.657046195940506" box). Rounding
      the data itself means the tap shows -17.7 — the same number you can see.
    """
    d = df.copy()
    for c in numeric:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce").round(1)
    if index_col in d.columns:
        d = d.set_index(index_col)
    # Columns that aren't percentages still need a format, or pandas prints the
    # full float (0.200000 instead of 0.2×).
    spec = {c: "{:+.1f}%" for c in numeric if c in d.columns}
    for c, f in (fmt or {}).items():
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
            spec[c] = f
    sty = d.style.map(colour, subset=[c for c in numeric if c in d.columns])
    if extra:
        sty = extra(sty)
    styled = sty.format(spec, na_rep="—")
    if select_key:
        ev = st.dataframe(styled, use_container_width=True, height=height,
                          on_select="rerun", selection_mode="single-row",
                          key=select_key)
        picked = list(getattr(ev, "selection", {}).get("rows", []) or [])
        return d.index[picked[0]] if picked else None
    st.dataframe(styled, use_container_width=True, height=height)
    return None


STATE_LBL = {"uptrend": "🪜 uptrend", "stalling": "⚠️ losing steam",
             "uptrend_broken": "🔻 uptrend broke", "downtrend": "❄️ downtrend",
             "basing": "〰️ basing", "sideways": "〰️ sideways"}



# ------------------------------------------------------- permanent watchlist
# Streamlit Cloud's filesystem is REBUILT from GitHub on every restart, so a
# local write only survives the session. Committing through the GitHub API makes
# an edit permanent AND keeps the Telegram bot in sync, since both read the same
# watchlist.txt from the repo.
def gh_config():
    try:
        tok = st.secrets.get("GITHUB_TOKEN")
        repo = st.secrets.get("GITHUB_REPO")
    except Exception:
        tok = repo = None
    return (tok, repo) if tok and repo else (None, None)


def gh_save_watchlist(tickers, message):
    """Commit watchlist.txt to GitHub. Returns (ok, detail)."""
    import base64
    import requests
    tok, repo = gh_config()
    if not tok:
        return False, "no GITHUB_TOKEN / GITHUB_REPO secret"
    url = f"https://api.github.com/repos/{repo}/contents/watchlist.txt"
    hdr = {"Authorization": f"Bearer {tok}",
           "Accept": "application/vnd.github+json"}
    try:
        cur = requests.get(url, headers=hdr, timeout=20)
        sha = cur.json().get("sha") if cur.status_code == 200 else None
        body = ("\n".join(tickers) + "\n").encode("utf-8")
        payload = {"message": message,
                   "content": base64.b64encode(body).decode("ascii")}
        if sha:
            payload["sha"] = sha
        r = requests.put(url, headers=hdr, json=payload, timeout=20)
        if r.status_code in (200, 201):
            return True, "committed to GitHub"
        return False, f"GitHub said {r.status_code}: {r.json().get('message','')}"
    except Exception as e:
        return False, str(e)


def s_name(t):
    return s.name_for(t) or ""


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
st.markdown('<div class="hdrow">', unsafe_allow_html=True)
c1, c2 = st.columns([8, 1])
with c1:
    st.title("👑 StockAlert")
    st.caption(f"Live · {now}")
with c2:
    if st.button("🔄", help="Refresh live data", key="refresh"):
        st.cache_data.clear()
        st.rerun()
st.markdown('</div>', unsafe_allow_html=True)

snap = get_snapshot()
rows = snap["rows"]

if snap.get("session_note"):
    st.markdown(f"<div class='sess'>🕒 {snap['session_note']}</div>",
                unsafe_allow_html=True)

# --------------------------------------------------------------- mood row
breadth = snap.get("breadth")
adv = sum(1 for r in rows if (r.get("day") or 0) > 0)
mood = "—"
if breadth is not None:
    mood = ("🟢 Risk-ON" if breadth >= 65 else "🙂 Positive" if breadth >= 55
            else "😐 Mixed" if breadth >= 45 else "🔴 Risk-OFF")


def _chip(label, value, v=None):
    """One compact stat. st.metric stacks into a tall block on a phone and ate
    most of the screen, so the header is a single wrapping row instead."""
    c = "#16a34a" if (v or 0) > 0 else ("#dc2626" if (v or 0) < 0 else "inherit")
    return (f"<span class='chip'><span class='lbl'>{label}</span>"
            f"<span class='val' style='color:{c}'>{value}</span></span>")


idx = (snap.get("indices") or [])[:2]
bits = [_chip("Mood", f"{mood} · {breadth:.0f}% adv" if breadth is not None else mood)]
for nm, v in idx:
    bits.append(_chip(nm, pct(v), v))
bits.append(_chip("Scanned", f"{len(rows)} · {adv}↑ {len(rows)-adv}↓"))
st.markdown("<div class='hdr'>" + "".join(bits) + "</div>", unsafe_allow_html=True)

# ------------------------------------------------------- market map (top)
# Sectors and Baskets answer "where is money going" — context you glance at
# before picking anything. They sit in their own row so the tab bar below stays
# purely about choosing a stock.
# Hidden by default. st.pills gives small side-by-side pills with a SUBTLE
# selected state and clears when you tap the active one again.
_map = st.pills("Market map", ["🌡️ Sectors", "🧲 Baskets"],
                selection_mode="single", default=None,
                label_visibility="collapsed")

# ------------------------------------------------------------- 1. sectors
if _map == "🌡️ Sectors":
    st.subheader("Where money is rotating")
    sm = mm.sector_multi()
    secdf = pd.DataFrame([{
        "Sector": r["name"], "4h %": r["4h"], "Today %": r["1d"],
        "1wk %": r["1w"], "2wk %": r["2w"], "1mo %": r["1m"],
        "Trend": "🟢 up" if r["strong"] else "🔴 weak",
    } for r in sm])
    # Click a sector row to open it — no separate dropdown needed.
    pick = show_table(secdf, ["4h %", "Today %", "1wk %", "2wk %", "1mo %"],
                      index_col="Sector", select_key="sectorpick")
    st.caption("👆 Tap a sector to see the stocks driving it. "
               "Trend = the sector ETF vs its own 50-day line.")

    if pick:
        mem = mm.sector_members(snap, pick, n=10)
        if not mem:
            st.info(f"No {pick} stocks in the scanned universe yet.")
        else:
            row = next((r for r in sm if r["name"] == pick), {})
            st.markdown(
                f"<div class='sechd'>{pick} · today "
                f"{(row.get('1d') or 0):+.2f}% · 1wk {(row.get('1w') or 0):+.1f}%"
                f" · {len(mem)} driving it</div>", unsafe_allow_html=True)
            mdf = pd.DataFrame([{
                "Ticker": r["ticker"], "Name": s.name_for(r["ticker"]) or "",
                "Today %": r["day"], "Money×": r.get("money"),
                "RVOL": r.get("rvol"),
                "1wk %": (r.get("spans") or {}).get("1w"),
                "1mo %": (r.get("spans") or {}).get("1m"),
                "Trend": STATE_LBL.get((r.get("struct") or {}).get("state"), ""),
            } for r in mem])
            show_table(mdf, ["Today %", "1wk %", "1mo %"],
                       fmt={"Money×": "{:.1f}×", "RVOL": "{:.1f}×"})
            st.caption("Ranked by PULL — the move weighted by the money behind "
                       "it. A 5% pop on thin volume shifts a sector far less "
                       "than a 2% push on 3× normal money.")

if _map == "🧲 Baskets":
    st.subheader("What big money is trading as one basket")
    st.caption("Found by measuring which stocks MOVE TOGETHER (4 weeks of daily "
               "returns) — not from any fixed sector list. A basket spanning "
               "several sectors means money is rotating by THEME.")
    groups = mm.flow_clusters(snap, max_groups=6)
    if not groups:
        st.info("No clear baskets right now — today's moves look stock-specific "
                "rather than a coordinated rotation. (Quiet before the US open.)")
    for g in groups:
        tag = "BUYING" if g["up"] else "SELLING"
        dot = "🟢" if g["up"] else "🔴"
        col = "#16a34a" if g["up"] else "#dc2626"
        names = "".join(
            f"<span class='pair'><b>{t}</b>&nbsp;<span class='nm'>"
            f"{s_name(t)}</span></span>" for t in g["members"][:12])
        spread = ", ".join(g["sectors"][:3])
        note = ("cuts across sectors" if len(g["sectors"]) > 1 else "one sector")
        st.markdown(
            f"""<div class='card'>
                 <div class='crow'>
                   <span class='tag' style='color:{col}'>{dot} {tag}</span>
                   <span class='big' style='color:{col}'>{g['avg']:+.1f}%</span>
                   <span class='sub'>{g['money']:.1f}× money · {len(g['members'])} stocks</span>
                 </div>
                 <div class='names'>{names}</div>
                 <div class='foot'>{spread} — {note}</div>
               </div>""", unsafe_allow_html=True)
    if groups:
        movers = sum(1 for r in rows if abs(r.get("day") or 0) >= 1.0)
        note = ("Only groups of 3+ stocks that move together AND are moving the "
                "same way today. A basket spanning several sectors is the useful "
                "case — money rotating by theme.")
        if len(groups) < 3:
            # Be explicit rather than leaving her wondering if it's broken.
            note += (f" Few baskets right now because only {movers} of {len(rows)} "
                     "stocks have moved 1%+ today — baskets need real movement to "
                     "form, so expect more once the US session is running.")
        st.caption(note)

st.divider()
tabs = st.tabs(["🔥 Hot money", "🏆 Strongest", "👑 Dip ranking", "🔎 Stock",
                "📋 Watchlist"])

# ----------------------------------------------------------- 2. hot money
with tabs[0]:
    st.subheader("Where the money is going today")
    st.caption("Ranked by HEAT — money flow, speed off the low, trend structure "
               "and sector — not just today's %. That is what makes it different "
               "from Strongest 1d, which sorts purely on the move.")
    hot = mm.hot_ranked(snap)[:20]
    STATE = STATE_LBL
    hdf = pd.DataFrame([{
        "Ticker": r["ticker"], "Name": s.name_for(r["ticker"]) or "",
        "Today %": r["day"], "Money×": r.get("money"), "RVOL": r.get("rvol"),
        "Off low %": r.get("recover"),
        "1wk %": (r.get("spans") or {}).get("1w"),
        "2wk %": (r.get("spans") or {}).get("2w"),
        "1mo %": (r.get("spans") or {}).get("1m"),
        "Trend": STATE.get((r.get("struct") or {}).get("state"), ""),
        "Sector": r.get("sector") or "",
    } for r in hot])
    show_table(hdf, ["Today %", "Off low %", "1wk %", "2wk %", "1mo %"],
               fmt={"Money×": "{:.1f}×", "RVOL": "{:.1f}×"})
    st.caption("**Money×** = *euros* traded today vs this stock's own normal. "
               "**RVOL** = *shares* traded vs normal. They usually agree; Money× "
               "is the better one because it counts the actual capital committed. Above "
               "~2× means real buyers, not drift. A big move on ~1× volume is not "
               "backed by institutions. Momentum needs a longer hold — see /mode wide.")

# ------------------------------------------------------------ 4. strongest
with tabs[1]:
    st.subheader("Strongest over a chosen window")
    win = st.radio("Window", ["1d", "2d", "3d", "1w", "2w", "3w", "1m"],
                   index=3, horizontal=True)
    sel = [r for r in rows if (r.get("spans") or {}).get(win) is not None]
    sel.sort(key=lambda r: r["spans"][win], reverse=True)
    # Show the chosen window first, then the others — WITHOUT repeating it.
    # (Picking "1d" used to produce a duplicate "1d %" column, which makes the
    # pandas styler hand back a DataFrame instead of a Series and blow up.)
    extras = [k for k in ("1d", "1w", "2w", "1m") if k != win]
    labels = {"1d": "1d %", "2d": "2d %", "3d": "3d %", "1w": "1wk %",
              "2w": "2wk %", "3w": "3wk %", "1m": "1mo %"}
    numcols = [f"▶ {labels[win]}"] + [labels[k] for k in extras]
    sdf2 = pd.DataFrame([dict(
        [("Ticker", r["ticker"]), ("Name", s.name_for(r["ticker"]) or ""),
         (f"▶ {labels[win]}", r["spans"][win])]
        + [(labels[k], (r.get("spans") or {}).get(k)) for k in extras]
        + [("Trend", STATE.get((r.get("struct") or {}).get("state"), "")),
           ("Sector", r.get("sector") or "")]
    ) for r in sel[:20]])
    show_table(sdf2, numcols)

# ---------------------------------------------------------- 5. dip ranking
with tabs[2]:
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
    for _c, _n in (("Price", 2), ("RSI", 0), ("Room", 1), ("In range %", 0)):
        rdf[_c] = pd.to_numeric(rdf[_c], errors="coerce").round(_n)
    rdf = rdf.set_index("Ticker")
    st.dataframe(
        rdf.style.map(score_colour, subset=["Score"])
           .format({"Price": "{:.2f}", "RSI": "{:.0f}", "Room": "{:.1f}R",
                    "In range %": "{:.0f}%"}, na_rep="—"),
        use_container_width=True, height=560)
    st.caption("**Room** = how far to the next resistance, in units of your stop. "
               "Under 1R the target is blocked. **In range %** under 40 = a real "
               "pullback; over 55 = you'd be chasing.")

# -------------------------------------------------------------- 6. one stock
with tabs[3]:
    st.subheader("Full breakdown")
    sym = st.text_input("Ticker(s)", value="",
                        placeholder="NVDA   ·   or several: NVDA, SAP.DE, TTE.PA")
    quick = st.session_state.get("quick_sym")
    if quick and not sym:
        sym = quick
        st.session_state["quick_sym"] = None

    if sym:
        syms = [x.strip().upper() for x in sym.replace(";", ",").split(",")
                if x.strip()][:5]
        # NB: must be a real container — the `st` module itself is not a context
        # manager, which crashed the single-ticker case.
        cols = st.columns(len(syms)) if len(syms) > 1 else [st.container()]
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


# --------------------------------------------------------------- 7. watchlist
with tabs[4]:
    st.subheader("Your watchlist")
    wl = [t for t in s.load_watchlist()]
    st.caption(f"{len(wl)} tickers. SPY and QQQ are kept as market context.")

    _tok, _repo = gh_config()

    def _persist(new_list, msg):
        """Write locally (instant) and commit to GitHub (permanent)."""
        with open(s.WATCHLIST_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(new_list) + "\n")
        st.cache_data.clear()
        if _tok:
            ok, detail = gh_save_watchlist(new_list, msg)
            if ok:
                st.success(f"{msg} — saved permanently ({_repo}).")
            else:
                st.warning(f"{msg} — saved for this session only ({detail}).")
        else:
            st.info(f"{msg} — this session only. Add GITHUB_TOKEN + GITHUB_REPO "
                    "secrets to make edits permanent.")

    c_add, c_del = st.columns(2)
    with c_add:
        new = st.text_input("Add ticker", placeholder="NVDA  ·  SAP.DE  ·  TTE.PA",
                            key="wl_add")
        if st.button("➕ Add", key="wl_add_btn") and new.strip():
            sym = new.strip().upper()
            if sym in wl:
                st.warning(f"{sym} is already on the list.")
            else:
                try:
                    ok = not s.yf.download(sym, period="5d", interval="1d",
                                           progress=False).empty
                except Exception:
                    ok = False
                if not ok:
                    st.error(f"{sym} — no Yahoo data. EU names need a suffix "
                             "(.DE .AS .PA .L .F).")
                else:
                    _persist(wl + [sym], f"Add {sym} to watchlist")
                    st.rerun()
    with c_del:
        rm = st.multiselect("Remove tickers",
                            [t for t in wl if t not in ("SPY", "QQQ")],
                            key="wl_rm")
        if st.button("➖ Remove selected", key="wl_rm_btn") and rm:
            _persist([t for t in wl if t not in rm],
                     f"Remove {', '.join(rm)} from watchlist")
            st.rerun()

    if _tok:
        st.caption(f"✅ Edits are committed to **{_repo}** — permanent, and the "
                   "Telegram bot picks them up on its next run.")
    else:
        st.warning("Edits here last for this session only. To make them "
                   "permanent, add two Streamlit secrets — `GITHUB_TOKEN` (a "
                   "GitHub token with repo access) and `GITHUB_REPO` "
                   "(e.g. \"GraceVio/StockAlert\") — or use /add and /remove "
                   "in Telegram.")

    group = st.toggle("Group by sector", value=True, key="wl_group")
    wdf = pd.DataFrame([{"Ticker": t, "Name": s.name_for(t) or "",
                         "Sector": s.SECTOR_MAP.get(t, "—")} for t in wl])
    if group:
        wdf = wdf.sort_values(["Sector", "Ticker"], kind="stable")
        for sec_name, chunk in wdf.groupby("Sector", sort=False):
            with st.expander(f"{sec_name}  ·  {len(chunk)}", expanded=False):
                st.dataframe(chunk[["Ticker", "Name"]].set_index("Ticker"),
                             use_container_width=True)
    else:
        st.dataframe(wdf.sort_values("Ticker").set_index("Ticker"),
                     use_container_width=True, height=560)

st.divider()
st.caption("Scores rate ENTRY QUALITY right now — they are not price predictions. "
           "Position sizing and stops matter more to your P&L than picking #1 over #5.")
