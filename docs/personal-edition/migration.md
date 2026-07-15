# Personal Edition migration

## Stages

1. Add typed TOML configuration, the legacy environment converter and Night Desk-only packaging. Keep the old Compose file runnable for one release.
2. Run `optix-worker` against offline fixtures and private databases. Compare its outputs with the old independent workers; never let both write the same production database.
3. Stop old Option Pro workers, start the unified worker and retain the previous release tag and Compose file for rollback.
4. Enable MacroLens ETL-only endpoints. Option Pro audits compatible legacy analysis in its local database and imports only a result whose current content identity, model, prompt schema and Simplified Chinese output all match. Every new analysis is then created locally.
5. Observe one full United States trading week before deleting the legacy runtime and the compatibility adapter.

## Database handling

- Back up every SQLite database before each stage with the SQLite backup API.
- Record schema versions, table counts, SHA-256 checksums, `quick_check`, `integrity_check` and `foreign_key_check`.
- Roll back code and containers, not database files. Additive migrations must leave old rows readable.
- Preserve response identifiers and `submission_outcome_unknown`; never replay an uncertain paid submission.
- Keep legacy Catalyst tables intact during the first release. The importer reads them without changing or deleting old rows and records every accepted or rejected candidate in the local audit table.
- Importing a legacy result does not consume the new daily submission allowance. Superseded news revisions, English output and obsolete prompt schemas remain hidden.

## Configuration conversion

Run:

```bash
python -m app.tools.migrate_personal_config .env --output-directory config/migrated
```

The command writes four files:

- `personal.toml`: behavior, including access mode, AI limits, schedule and retention;
- `secrets.env`: only `OPENAI_API_KEY`, `FINNHUB_API_KEY`, `MARKETDATA_TOKEN`, `INTERNAL_API_TOKEN` and `APP_PASSWORD_HASH`;
- `machine.env`: `HOST_BIND`, `PORT`, `MACROLENS_URL`, `ALLOWED_HOSTS`, `TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_CIDRS` and `DATA_DIR`;
- `migration-report.json`: key names and migration status only.

The last three files use mode `0600`. The report never contains values, value lengths, hashes, URL values or secret fragments. `MARKETDATA_API_TOKEN` migrates to `MARKETDATA_TOKEN`, `MACROLENS_BASE_URL` migrates to `MACROLENS_URL`, and `MACROLENS_INTERNAL_TOKEN` migrates to `INTERNAL_API_TOKEN`. If either old and new name is present with a different non-empty value, conversion stops and records only the conflicting key names.

Old browser and HMAC credentials are not copied. They appear under `removed_keys` with status `removed_by_personal_edition`. If `APP_AUTH_TOKEN` exists without `APP_PASSWORD_HASH`, the report sets `requires_owner_password=true`; configure the replacement with:

```bash
./personal.sh secrets set APP_PASSWORD_HASH
```

Manage the other server-only values with the same command, for example `./personal.sh secrets set MARKETDATA_TOKEN`. After changing a secret, running containers are recreated so Compose rereads `secrets.env`; a plain restart is not used. Run `./personal.sh doctor` before deployment to apply the same access-boundary validation used by the application and deployment script.

## Access boundary

`private_network` is for direct loopback, SSH forwarding, RFC1918, Tailscale, WireGuard or IPv6 unique-local access. It requires `TRUST_PROXY_HEADERS=false`; `HOST_BIND` and `ALLOWED_HOSTS` may contain only approved private IP literals or localhost. DNS names and wildcard binds fail startup.

Every HTTP reverse proxy, public domain, Cloudflare Tunnel or public load balancer must use `password`. This mode requires a valid `APP_PASSWORD_HASH` and explicit `ALLOWED_HOSTS`. When proxy headers are enabled, `TRUSTED_PROXY_CIDRS` must contain only the actual private proxy source networks. Public catch-all networks such as `0.0.0.0/0` are rejected. A public domain such as `option.openweb-ui.xyz` therefore always uses password mode and HTTPS.

The compatibility adapter is removed after the first Personal Edition production release.

## Rollback

Each pull request has a release tag. To roll back, stop the new process, check out the preceding tag, restore its Compose definition and start it against the same forward-compatible databases. Do not restore an older database over newer task history.
