---
name: sri-scraper-dev
description: Capabilities and guidelines for working with the SRI Scraper codebase
---

# SRI Scraper Skill

Multi-tenant RPA system that automates downloading electronic invoices (comprobantes electrónicos) from Ecuador's SRI portal.

## Architecture & Overview

- **Python 3.11+** web scraper targeting `srienlinea.sri.gob.ec`
- **Dual engine**: Playwright (stable) + nodriver (undetected Chromium) with automatic fallback
- **CAPTCHA resolution**: Multi-provider (CapSolver, 2Captcha) + assisted manual mode via VNC
- **Task queue**: Celery + Redis for background job processing
- **Database**: PostgreSQL 15+ with async SQLAlchemy 2.0
- **API**: FastAPI REST endpoints for tenant management, scraping triggers, and exports
- **Scheduler**: Daily automated scraping at 06:30 Ecuador time
- **Docker**: Full containerized deployment with Xvfb for headed browser in containers

## Key Components

| Directory | Purpose |
|-----------|---------|
| `scrapers/engine.py` | Playwright-based scraper engine (primary) |
| `scrapers/nodriver_engine.py` | nodriver-based scraper engine (undetected, alternative) |
| `scrapers/portal.py` | SRI portal selectors, URLs, JS assets |
| `scrapers/captcha_strategy.py` | CAPTCHA attempt plan builder |
| `scrapers/js/` | Injected JS assets (controlled_query, extract_site_key, etc.) |
| `api/` | FastAPI REST API (tenants, comprobantes, ejecuciones, exports) |
| `tasks/scrape_tasks.py` | Celery task orchestration with engine fallback |
| `parsers/xml_parser.py` | XML parsing for all 6 document types |
| `parsers/types/` | Per-document-type parsers (factura, retencion, etc.) |
| `db/models/` | SQLAlchemy models (Tenant, Comprobante, Detalle, Retencion, Pago, EjecucionLog) |
| `captcha/` | CAPTCHA provider clients (CapSolver, 2Captcha) |
| `exporters/` | Excel export (3-sheet: headers, details, retentions) |
| `scheduler/jobs.py` | APScheduler / Celery Beat cron jobs |
| `config/settings.py` | Pydantic Settings from `.env` |
| `utils/` | Crypto, delays, screenshots, XML storage, browser env |
| `alembic/` | Database migrations |
| `deploy/` | VPS deployment scripts (DigitalOcean) |

## Engine Fallback Strategy

The system uses a **primary + fallback** engine approach:
1. If `BROWSER_PREFER_NODRIVER=true` → tries nodriver first, falls back to Playwright
2. If `BROWSER_PREFER_NODRIVER=false` → tries Playwright first, falls back to nodriver
3. If both engines fail, the primary exception is re-raised for Celery retry handling

## Scraping Flow

```
Login (Keycloak SSO) → Navigate to comprobantes recibidos →
Select filters (year, month, day, type) → Resolve CAPTCHA →
Extract access keys (claves de acceso, 49 digits) → Download XMLs via SOAP →
Paginate through results → Persist to PostgreSQL + filesystem
```

## Document Types Supported

- Factura (Invoice)
- Liquidación de compra (Purchase liquidation)
- Nota de Crédito (Credit note)
- Nota de Débito (Debit note)
- Comprobante de Retención (Retention certificate)
- Guía de Remisión (Shipping guide)

## Key Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL async connection string |
| `REDIS_URL` | Redis for Celery broker |
| `SECRET_KEY` | Fernet encryption key (min 32 chars) |
| `CAPTCHA_PROVIDER` | `capsolver` or `2captcha` |
| `CAPSOLVER_API_KEY` | CapSolver API key |
| `TWOCAPTCHA_API_KEY` | 2Captcha API key |
| `CAPTCHA_ASSISTED_MODE` | `off`, `fallback`, or `only` |
| `BROWSER_PREFER_NODRIVER` | `true` to use nodriver as primary engine |
| `PLAYWRIGHT_HEADLESS` | `true` for headless mode |

## Adaptive Learning System (`scrapers/adaptive_strategy.py`)

The system **learns from every interaction with SRI** and auto-adjusts to evade bot detection:

### How It Works
- **Engine scoring**: Tracks success/failure/block rates per engine (nodriver vs Playwright). Automatically selects the engine with highest weighted score.
- **CAPTCHA variant reordering**: Sorts CAPTCHA variants by historical success rate. Variants that get blocked are pushed to the end or put in cooldown.
- **Timing analysis**: Records success by hour of day to identify optimal scraping windows.
- **Adaptive delays**: When SRI blocks frequently, delays automatically increase (up to 2.5x multiplier). When things are smooth, delays stay normal.
- **Cooldown system**: Engines/variants with 3+ recent blocks enter cooldown (2 hours). System automatically switches to alternatives.
- **Provider health**: Tracks per-provider reliability. Failed providers are deprioritized.

### Redis Keys
- `sri:adaptive:engine:{name}` — per-engine stats (7-day TTL)
- `sri:adaptive:variant:{name}` — per-CAPTCHA-variant stats (7-day TTL)
- `sri:adaptive:provider:{name}` — per-provider stats (7-day TTL)
- `sri:adaptive:timing:{HH}` — per-hour success rates (7-day TTL)
- `sri:adaptive:blocks:{source}` — recent block events (2-hour TTL)

### Monitoring
- `GET /health` includes `adaptive` section with current strategy summary
- Logs: `adaptive_engine_seleccion`, `adaptive_variants_reordenados`, `adaptive_delays_ajustados`

## Persistent Knowledge Base (`scrapers/knowledge_base.py`)

Two-tier learning system. Redis handles **short-term** reaction (7-day TTL). PostgreSQL stores **long-term** patterns permanently.

### Database Tables
- `sri_knowledge_base` — Aggregated stats per category/key (engine, variant, provider, timing). Never expires.
- `sri_block_events` — Individual block events with full context (engine, error type, hour, day, variant, provider). Used for pattern mining.

### Capabilities
- **Variant blacklisting**: Variants with <20% long-term success rate are permanently deprioritized
- **Dangerous hour detection**: Identifies hours where block rate exceeds 40% (last 30 days)
- **Provider ranking**: Long-term provider reliability ranking
- **Block pattern analysis**: Detects most common error types, worst engine+variant combos, hourly/daily block distribution
- **Full summary**: `GET /health` → `knowledge_base` section

### How It Feeds Into Adaptive Strategy
- `AdaptiveStrategyTracker.get_ordered_variants()` consults KB blacklist before reordering
- Every engine result is recorded to both Redis (short-term) and PostgreSQL (long-term)
- Every block event is stored with full context for future pattern mining

### Key Files
| File | Purpose |
|------|---------|
| `db/models/knowledge_base.py` | `KnowledgeEntry` + `BlockEvent` SQLAlchemy models |
| `scrapers/knowledge_base.py` | `SRIKnowledgeBase` service (queries, pattern detection) |
| `scrapers/adaptive_strategy.py` | Short-term Redis tracker (consults KB for blacklists) |
| `tasks/scrape_tasks.py` | Records every result to both tiers |

## Anti-Detection Measures

### nodriver Engine (`scrapers/nodriver_engine.py`)
- **Undetected Chromium**: Uses `nodriver` which patches Chromium to bypass bot detection
- **Stealth patches**: Hides `navigator.webdriver`, fakes plugins/languages, spoofs WebGL fingerprint
- **Canvas noise**: Subtle randomization to prevent fingerprinting
- **Chrome runtime spoof**: Fakes `window.chrome` object missing in headless
- **Human-like behavior**: Bezier curve mouse movements, random scroll, form interaction delays
- **Dialog auto-dismiss**: Intercepts `alert()`/`confirm()` to prevent DOM blocking

### Playwright Engine (`scrapers/engine.py`)
- **playwright-stealth**: Applies stealth plugin patches
- **Persistent profiles**: Reuses browser profiles per tenant (builds cookie/history trust)
- **GPU spoofing**: Rotates WebGL vendor/renderer strings

### Both Engines
- **Adaptive delay multiplier**: Slows down when SRI blocks frequently
- **Session cleanup**: Clears cookies/cache between runs
- **Human-like delays**: Randomized waits between 0.8-2.5s (adjustable)

## Debugging Guide

1. **CAPTCHA issues**: Check provider API key balance. Look for `proveedor_balance_cero_o_invalido` in logs. The `ProviderError` exception triggers automatic provider switching.
2. **Engine failures**: Check logs for `motor_primario_bloqueado_intentando_fallback`. Both engines are tried automatically. If both fail, check `ambos_motores_fallaron`.
3. **Adaptive learning**: Check `GET /health` → `adaptive` section for current strategy state. Check `adaptive_engine_seleccion` in logs.
4. **nodriver startup**: Verify Xvfb is running in Docker (`DISPLAY=:99`). Check `--no-sandbox` flag. Look for `arrancando_nodriver` log.
5. **Login failures**: `SRILoginError` — verify SRI credentials. Check `estado_post_login` log for redirect URL.
6. **Session expiry**: `SRISessionExpiredError` — automatic re-login on retry. Check `auth/realms` in URL.
7. **Maintenance window**: `SRIMaintenanceError` — SRI is down (typically 00:00-06:00 Ecuador). Retry after 30 min.
8. **DB connection**: Verify PostgreSQL is healthy before app starts. Check `DATABASE_URL` in `.env`.
9. **XML download fails**: SOAP endpoint may be down. Check `soap_rechazado_fallback_lnkxml` log.
10. **Circuit breaker open**: Too many consecutive failures. Check `sri:circuit:state` in Redis. Resets after 30 min.
11. **High block rate**: Check `adaptive_delays_ajustados` — system auto-increases delays. If persistent, consider switching `CAPTCHA_ASSISTED_MODE=fallback` for manual intervention.

## Error Handling & Retries

| Exception | Action | Retry |
|-----------|--------|-------|
| `SRILoginError` | Bad credentials | No retry |
| `SRICaptchaError` | CAPTCHA failed | 5 min backoff |
| `SRIMaintenanceError` | SRI down | 30 min wait |
| `SRITimeoutError` | Network timeout | Exponential backoff |
| `SRISessionExpiredError` | Re-login needed | Immediate retry |
| `SRIDownloadError` | XML download failed | 3 attempts |
| `ProviderError` | Provider balance/key issue | Switch provider |

## Running Locally

```bash
docker-compose up -d postgres redis
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
alembic upgrade head
uvicorn api.main:app --reload                           # API
celery -A tasks.celery_app worker --loglevel=info       # Worker
celery -A tasks.celery_app beat --loglevel=info         # Scheduler
```

## Testing

```bash
pytest tests/ -x -q
```
