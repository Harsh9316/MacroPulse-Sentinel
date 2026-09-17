# 🚨 MacroPulse Sentinel

> **Production-grade async event-driven economic news monitor**  
> LLM-powered sentiment → structured Buy/Sell alerts → Telegram

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      MacroPulse Sentinel                        │
│                                                                 │
│  ┌───────────────┐  ┌────────────────┐  ┌────────────────────┐ │
│  │ Economic Cal  │  │ RSS Monitor    │  │ Twitter/X Monitor  │ │
│  │ (FF JSON API) │  │ (15+ feeds)    │  │ (API v2 / Nitter)  │ │
│  └──────┬────────┘  └───────┬────────┘  └─────────┬──────────┘ │
│         └──────────────┬────┘                     │            │
│                        ▼                           │            │
│                ┌───────────────┐◄──────────────────┘            │
│                │ Event Bus     │  asyncio.Queue(maxsize=200)    │
│                └───────┬───────┘                                │
│                        ▼                                        │
│                ┌───────────────┐                                │
│                │ Dedup Cache   │  SQLite + SHA-256 + 24h TTL   │
│                └───────┬───────┘                                │
│                        ▼                                        │
│                ┌───────────────┐  Price Feed (Twelve Data WS)  │
│                │ LLM Sentinel  │◄─ XAU/USD · NAS100 · USD/JPY  │
│                │ Groq primary  │                                │
│                │ OAI fallback  │                                │
│                │ Anthropic fb  │                                │
│                └───────┬───────┘                                │
│                        ▼                                        │
│                ┌───────────────┐                                │
│                │ Risk Calc     │  $25 exact lot sizing          │
│                └───────┬───────┘                                │
│                        ▼                                        │
│                ┌───────────────┐                                │
│                │ Telegram Bot  │  Rate-limited HTML alerts      │
│                └───────────────┘                                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Sources

### Economic Calendar
| Source | Endpoint | Interval |
|--------|----------|----------|
| Forex Factory | JSON calendar API | 5s (fast mode near events) / 60s |

### RSS Feeds (15 feeds)
| Source | Twitter |
|--------|---------|
| Federal Reserve Press Releases | `@federalreserve` |
| Federal Reserve Speeches | `@federalreserve` |
| ECB Press Releases | `@ecb` / `@Lagarde` |
| Bank of England News | `@bankofengland` |
| Bank of Japan Announcements | `@Bank_of_Japan_e` |
| US Treasury | `@USTreasury` / `@JanetYellen` |
| Reuters Top News | `@Reuters` |
| Reuters Business | `@ReutersBiz` |
| Reuters Markets | `@ReutersBiz` |
| ForexLive | `@ForexLive` |
| FXStreet | `@FXStreet` |
| Investing.com | — |
| MarketWatch | `@MarketWatch` |
| WSJ Markets | `@WSJmarkets` |
| ZeroHedge | `@zerohedge` |

### Twitter/X Accounts Monitored
#### Central Banks (CRITICAL tier)
| Handle | Account |
|--------|---------|
| `@federalreserve` | US Federal Reserve |
| `@ecb` | European Central Bank |
| `@bankofengland` | Bank of England |
| `@Bank_of_Japan_e` | Bank of Japan (English) |
| `@SNB_BNS` | Swiss National Bank |
| `@RBAInfo` | Reserve Bank of Australia |
| `@bankofcanada` | Bank of Canada |

#### Key Officials
| Handle | Person |
|--------|--------|
| `@Lagarde` | Christine Lagarde (ECB President) |
| `@JanetYellen` | Janet Yellen (US Treasury Secretary) |
| `@Nick_Timiraos` | Nick Timiraos (WSJ — "Fed Whisperer") |
| `@GregIpWsj` | Greg Ip (WSJ Chief Economics Commentator) |

#### Financial Wires
| Handle | Wire |
|--------|------|
| `@Reuters` | Reuters |
| `@ReutersBiz` | Reuters Business |
| `@Bloomberg` | Bloomberg |
| `@markets` | Bloomberg Markets |
| `@FinancialJuice` | FinancialJuice (ultra-low-latency) |
| `@ForexLive` | ForexLive |
| `@WSJmarkets` | WSJ Markets |
| `@CNBC` | CNBC |
| `@zerohedge` | ZeroHedge |
| `@FXStreet` | FXStreet |

---

## Quick Start

### 1. Install dependencies

```bash
cd macropulse_sentinel

# Using uv (recommended)
pip install uv
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# OR using pip
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

**Minimum required** (dry-run testing):
```env
GROQ_API_KEY=gsk_...
DRY_RUN=true
PRICE_FEED_PROVIDER=mock
TWITTER_MODE=disabled
```

**Full production**:
```env
GROQ_API_KEY=gsk_...
OPENAI_API_KEY=sk-...          # fallback
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=-1001...
TWELVEDATA_API_KEY=...
TWITTER_BEARER_TOKEN=...
TWITTER_MODE=api               # or rss
PRICE_FEED_PROVIDER=twelvedata
DRY_RUN=false
```

### 3. Test Telegram connection

```bash
python -m macropulse.telegram.bot --test
```

### 4. Run

```bash
# Dry run (no Telegram messages)
DRY_RUN=true PRICE_FEED_PROVIDER=mock python main.py

# Live
python main.py
```

### 5. Run tests

```bash
pytest tests/ -v
```

---

## Position Sizing Math

| Symbol | Pip Size | Value/Lot | Formula |
|--------|----------|-----------|---------|
| XAU/USD | 0.01 ($) | $1.00/pip | `lot = 25 / (SL_pips × 1.00)` |
| NAS100 | 1.0 (pt) | $1.00/pt | `lot = 25 / (SL_pts × 1.00)` |
| USD/JPY | 0.01 (¥) | ≈$6.67/pip at 150 | `lot = 25 / (SL_pips × (1000/rate))` |

> **Example — NFP Beat:**  
> XAU/USD SELL: Entry 2340.00, SL 2340.50 (50 pips) → lot = 25/50 = **0.50 lots**

---

## Risk Controls

- ✅ Max $25 USD risk per trade (enforced by calculator, not LLM)
- ✅ Minimum R:R = 2.0:1 (signals below threshold → STAND_ASIDE)
- ✅ Minimum SL distances (XAU: 50 pips, NAS100: 15 pts, USD/JPY: 10 pips)
- ✅ Daily loss circuit breaker at $750 (stops all signals for the day)
- ✅ Dedup cache prevents duplicate signals for same event within 24h
- ✅ Directional validation (SL must be correct side of entry)

---

## LLM Provider Fallback Chain

```
Groq (llama-3-3-70b) ──► OpenAI (gpt-4o-mini) ──► Anthropic (claude-3-5-haiku)
         primary                 fallback 1                  fallback 2
         ~300ms                  ~800ms                      ~1.2s
```

Each provider has 3 retry attempts with exponential backoff before falling through.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | Groq API key (primary LLM) |
| `OPENAI_API_KEY` | — | OpenAI API key (fallback) |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (fallback) |
| `TELEGRAM_BOT_TOKEN` | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | — | Channel/group chat ID |
| `TWITTER_BEARER_TOKEN` | — | Twitter API v2 Bearer Token |
| `TWITTER_MODE` | `rss` | `api` \| `rss` \| `disabled` |
| `TWELVEDATA_API_KEY` | — | Twelve Data API key |
| `PRICE_FEED_PROVIDER` | `mock` | `twelvedata` \| `mock` |
| `MAX_RISK_PER_TRADE` | `25` | Dollar risk per trade |
| `MAX_DAILY_LOSS` | `750` | Daily loss circuit breaker |
| `DRY_RUN` | `false` | Suppress Telegram sends |
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` |

---

## Sample Alert Output

```
🚨 MACROPULSE SENTINEL ALERT 🚨
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔥 US NON-FARM PAYROLLS — MASSIVE BEAT
🌍 Currency: USD  Impact: HIGH
🕐 08:32 UTC  |  Source: Reuters Business

📌 MACRO ANALYSIS:
Payrolls exceeded forecast by 150k, signalling a hot labour
market that reduces Fed rate-cut probability and broadly
strengthens the dollar.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📐 TRADE SETUPS (2/3 actionable)

🥇 XAU/USD  🔴 📉 SELL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 MARKET ORDER
  ↳ Entry   : 2340.00
  ↳ Stop    : 2340.50  🛡 ($25.00 risk)
  ↳ TP-1    : 2338.50  💰 (+$75.00)
  ↳ TP-2    : 2337.00  💰 (+$150.00)
  ↳ R:R     : 3.0:1
  ↳ Lot     : 0.50 lots  ($25 risk)

📊 NAS100  🔴 📉 SELL
...
```
