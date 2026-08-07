# Trimmed stocks — notes

These were removed from the live `WATCHLIST` in `scanner.py`. They are **not
banned** — you can add any back by pasting the ticker into the watchlist. They
were trimmed on *principle* (liquidity, spread, data quality, volatility), NOT
because they "lost" in a 60-day backtest (too few trades to judge fairly).

Why trim? The dip-buy strategy needs: reliable 15-min data, tight spreads, and
trends that don't whipsaw the stop. Thin or hyper-volatile names fail on those.

## Removed — thin / small-cap / unreliable 15m data
| Ticker | Name | Reason |
|---|---|---|
| IREN | IREN Ltd | small crypto-miner, very volatile |
| IONQ | IonQ | quantum, speculative, whipsaws stops |
| QBTS | D-Wave Quantum | quantum, speculative |
| RGTI | Rigetti Computing | quantum, speculative |
| NVTS | Navitas Semiconductor | small-cap, thin |
| CRML | Critical Metals | tiny, illiquid |
| RCAT | Red Cat Holdings | small-cap, thin |
| SNEX | StoneX Group | thinner intraday volume |
| NBIS | Nebius Group | volatile, newly re-listed |
| RBRK | Rubrik | recent IPO, unsettled |
| ALAB | Astera Labs | recent IPO, very volatile |
| SPCX | SpaceX | brand-new listing, extreme volatility |
| XK4.F | Gabler Group | tiny March-2026 IPO, thin |
| SSIT.L | Seraphim Space | small UK trust, thin |
| RPI.L | Raspberry Pi | small, recent IPO |
| SMHN.DE | SÜSS MicroTec | small-cap German |
| GXI.DE | Gerresheimer | mid-cap, thinner |
| MUX.DE | Mutares | small-cap German |
| SDF.DE | K+S | volatile commodity mid-cap |

## Removed — liquid enough, but choppy / high whipsaw
| Ticker | Name | Reason |
|---|---|---|
| CRWV | CoreWeave | new, extreme swings |
| SHOP | Shopify | liquid but very choppy intraday |
| ZS | Zscaler | choppy |
| TEAM | Atlassian | gap-prone around news |
| UPST | Upstart | small-cap, huge swings |
| PODD | Insulet | mid-cap, gappy |
| RDDT | Reddit | recent IPO, headline-driven |
| HOOD | Robinhood | very volatile |
| SOFI | SoFi | small-cap, volatile |
| TTD | The Trade Desk | gap-prone around earnings |
| HPE | HP Enterprise | slow, weak trends |
| DDOG | Datadog | gap-prone |

## Added instead (liquid, clean-trending — better for this strategy)
SPY, QQQ (ETFs), MSFT, CRM, ADBE, COST, PEP, WMT, MCD, V, MA, JPM, UNH, HD,
CVX, and EU: MC.PA (LVMH), OR.PA (L'Oréal).

## Tradability
All *kept* names are tradable on **Trade Republic** (and Revolut). If you ever
add a name here back and it's not on Trade Republic, check Revolut — but all the
kept large-caps and the SPY/QQQ ETFs are available on both.

_Note: SPY/QQQ are US ETFs — on Trade Republic, EU rules may route you to a
UCITS equivalent (e.g. an S&P 500 / Nasdaq-100 ETF). The bot uses SPY/QQQ for
clean signals; you trade the equivalent ETF you have access to._
