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

## Target

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
- Raw news remains untrusted and in its source language. Derived title, summary and analysis fields use the `zh-CN` language contract and are the only fields shown by Night Desk.

## Configuration ownership

`config/personal.toml` owns behavior. Environment files contain only secrets and machine addresses. Domain modules receive typed configuration instead of interpreting unrelated environment switches.
