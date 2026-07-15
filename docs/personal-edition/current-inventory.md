# Personal Edition current inventory

Baseline date: 2026-07-15. Source revisions: option-pro `d04ef67703316c52279fb020e10278eb7e3e82f5`; MacroLens `a5e896d3d248cf658075a91baf9120c94f1d70c4`.

## Resident processes

| Host | Process | Responsibility | Persistent writer |
|---|---|---|---|
| Option Pro | backend | FastAPI and Night Desk | user requests and job creation |
| Option Pro | ai-worker | Responses jobs | `ai-jobs.db` |
| Option Pro | catalyst-sync-worker | remote health, news, calendar and action proxy | `catalyst-cache.db` |
| Option Pro | focus-context-producer | focus universe and snapshots | `catalyst-cache.db` |
| Option Pro | breakout-worker | discovery, lifecycle and snapshots | `optix.db` |
| MacroLens | backend | API, scheduler, news sources and calendar | `macrolens.db` |
| MacroLens | analysis-worker | news, calendar and market-focus model jobs | `macrolens.db` |
| MacroLens | frontend | React administration interface | none |

MacroLens also has a one-shot data initializer. The two repositories therefore run eight long-lived business processes before the personal refactor.

## Configuration inventory

- Option Pro `.env.example`: 189 declarations.
- MacroLens `.env.example`: 140 declarations.
- Option Pro Compose: 161 distinct variables and 272 explicit mappings.
- MacroLens Compose: 95 distinct variables.
- Option Pro has five `DEPLOY_REQUIRE_*` gates and about 28 behavior switches.
- Configuration is split across four Option Pro settings objects, the MacroLens settings object, database settings, direct environment reads, Compose defaults and deployment scripts.

Secrets, machine addresses and behavior are currently mixed. The Personal Edition boundary moves behavior into `config/personal.toml`; `.env` retains only secrets and host-specific addresses.

## Databases and writers

| Database | Current writers | Required preservation |
|---|---|---|
| `optix.db` | backend, breakout worker, focus producer | transactions, WAL, point-in-time scoring |
| `catalyst-cache.db` | catalyst sync and focus producer | last readable snapshot on sync failure |
| `ai-jobs.db` | backend and AI worker | idempotency, response identity, token usage |
| `macrolens.db` | backend scheduler and analysis worker | raw news, calendar and legacy analysis history |

The first release does not merge databases. Backups, `quick_check`, `integrity_check` and `foreign_key_check` remain mandatory.

## Feature flag dependency map

```text
MACROLENS_ENABLED
  -> CATALYST_MODE=display
  -> read credentials
  -> catalyst-sync-worker
  -> optional action credentials
  -> NEWS_LLM_MANUAL_ENABLED / HOT_CYCLE_* in MacroLens

FOCUS_PRODUCER_ENABLED
  -> focus-context-producer
  -> reverse HMAC endpoint
  -> MacroLens Focus Pull

BREAKOUT_RADAR_ENABLED
  -> breakout-worker

DEPLOY_REQUIRE_*
  -> deploy-only health gates for each independent process
```

The replacement is `catalyst_mode = off | read | manual | scheduled` plus one `breakout_enabled` boolean.

## Cross-service directions and authentication states

```text
Option Pro -- HMAC read/action --> MacroLens
Option Pro <-- HMAC focus pull -- MacroLens
```

The current protocol carries read and action key identifiers, previous secrets, nonces, clock skew, body hashes and three credential directions. The target is one HTTPS read-only direction from Option Pro to MacroLens with one bearer token.

## Model runtimes

- Option Pro has one Responses runtime for earnings, options and signal jobs.
- MacroLens has a second Responses runtime for news, calendar and market focus, plus retired Anthropic, Grok and Ollama provider code.
- Both currently default to GPT-5.6 Terra. The personal runtime must be GPT-5.6 Terra, reasoning `max`, background execution, concurrency one and four paid jobs per UTC day.
- Structured outputs, bounded untrusted input, idempotency, submission-unknown protection, response retrieval/cancellation and token usage must remain.

## Frontend entry points and dead code

Production `frontend/index.html` loads only `deck-api.js`, `deck-ai-jobs.js`, `deck-catalysts.js`, `deck-app.js`, `optix-deck.css` and `optix-catalysts.css`. PR 1 removed the old `app.js`, `pages/`, component tree, `styles.css` and v3 styles after replacing readiness checks and static tests. No production, container, documentation or test reference remains.

MacroLens's React/Vite/Tailwind frontend is a removal candidate; operational state moves to Night Desk and `/internal/v1/health`.

## Correctness constraints

The refactor must preserve strict schemas, untrusted-news boundaries, input limits, task idempotency, daily paid limits, token usage, `available_at`, `as_of`, completed-bar filtering, no-future replay, SQLite transactions and backups, old-snapshot readability, and the rule that news never changes the formal stock score by default.

## Main migration risks

1. A model submission can succeed before its response identifier is saved; such jobs must never be submitted again automatically.
2. Removing task leases before the single-process lock is active can create duplicate writers.
3. Changing prompt or language without changing task identity can reuse cached English results.
4. Deleting remote projections before importing recent visible analysis can blank Night Desk.
5. Removing old frontend files before health and static assertions change will make an otherwise healthy image fail readiness.
6. Raw English source text must remain available for traceability while every user-facing derived field is simplified Chinese.
