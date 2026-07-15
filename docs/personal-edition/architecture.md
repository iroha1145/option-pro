# Personal Edition architecture

## Current

```text
Browser -> Option Pro backend
             |-> ai-worker -> OpenAI
             |-> catalyst-sync-worker <-> MacroLens backend/scheduler
             |-> focus-context-producer -> MacroLens Focus Pull
             `-> breakout-worker

MacroLens backend -> analysis-worker -> OpenAI
MacroLens frontend -> MacroLens backend
```

## Personal Edition runtime

```text
Night Desk -> optix-web -> local SQLite
                  |
                  `-> optix-worker
                        |-> Breakout Radar
                        |-> Focus Context
                        |-> MacroLens raw-feed sync
                        |-> local ticker/event/hotspot logic
                        |-> GPT-5.6 Terra Responses jobs
                        `-> retention, backup and health

optix-worker -- HTTPS + bearer token --> macrolens
                                           |-> raw news sources
                                           |-> calendar
                                           `-> retention
```

There are three long-lived business processes. MacroLens cannot call Option Pro. Only Option Pro stores model results and derived news intelligence.

## Runtime rules

- `optix-web` serves the API and static Night Desk, reads databases and creates user-requested jobs. It does not run long loops or paid jobs.
- `optix-worker` owns one process lock and supervises isolated tasks. Every task records status, catches its own exception, backs off independently and observes graceful shutdown.
- `macrolens` runs one Uvicorn worker with one in-process scheduler. It fetches and deduplicates raw news, records source-native tickers and calendar data, and exposes four internal read-only endpoints.
- Model execution is OpenAI Responses API background mode only: `gpt-5.6-terra`, reasoning `max`, concurrency one, four paid submissions per UTC day.
- Raw news remains untrusted and in its source language. It is used only as internal evidence. Night Desk receives only Simplified Chinese titles, summaries and analysis that pass the `zh-CN` contract; pending items use Simplified Chinese waiting copy instead of exposing the raw source text.
- The local intelligence database keeps point-in-time news revisions, event groups, hotspot revisions and immutable focus snapshots. Model results are accepted only when their news revision or focus snapshot identity still matches.
- Scheduled mode uses durable Eastern Time slots at 08:00, 12:00 and 16:00. Read mode is the repository default and never creates paid jobs.
- Ticker attribution is restricted to the local canonical universe. Source tickers are hints, and model output cannot add a ticker outside the exact allowlist supplied with the job.

## Configuration ownership

`config/personal.toml` owns behavior. `machine.env` owns host-specific addresses and paths. `secrets.env` owns the five canonical server-only secrets. Exported process values have highest priority; otherwise loading order is `.env`, `machine.env`, then `secrets.env`. Domain modules receive typed configuration instead of interpreting unrelated environment switches.

The MacroLens connection uses canonical `MACROLENS_URL` and `INTERNAL_API_TOKEN` values. Legacy names are accepted only by the one-release migration adapter; no signing key identifier, nonce or previous secret remains in the Personal Edition route.

The application, `scripts/deploy.sh` and `./personal.sh doctor` call the same Python deployment validator. Direct private-network access never trusts forwarded headers. Any reverse-proxy or public-domain route uses password mode, explicit host names and narrowly scoped proxy source ranges.
