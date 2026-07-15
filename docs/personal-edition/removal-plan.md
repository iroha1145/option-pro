# Personal Edition removal plan

Every deletion requires repository search, import checks, route checks, frontend references, container entries, documentation and tests to reach zero or point to the replacement.

| Component | Replacement | Removal gate |
|---|---|---|
| old Option Pro pages, components and v3 styles | Night Desk deck files | production HTML, readiness and tests reference only deck files |
| `ai-worker`, catalyst sync, focus producer, breakout worker containers | `optix-worker` | fixture parity, isolation, process lock, graceful stop and health tests pass |
| MacroLens analysis worker and model providers | Option Pro local Responses runtime | recent visible analyses imported; no new legacy writes |
| MacroLens frontend | Night Desk Catalyst page and internal health | operational status is visible without the old UI |
| HMAC, nonces, key identifiers and previous secrets | HTTPS bearer token | four ETL endpoints pass token, outage and paging tests |
| reverse Focus Pull | Option Pro local focus ownership | MacroLens contains no Option Pro client import or route |
| remote analysis/action proxy tables | local analysis revisions and job history | old rows exported and read-only for 30-90 days |
| `DEPLOY_REQUIRE_*` and per-worker checks | aggregate worker health | deploy checks one web and one worker readiness surface |
| legacy environment adapter | typed TOML only | one production release has completed and unmapped reports are empty |

The deletion ledger is recorded in `deleted-components.md` as files are removed, including the prior purpose, replacement, reference evidence and rollback tag.
