# Personal Edition migration

## Stages

1. Add typed TOML configuration, the legacy environment converter and Night Desk-only packaging. Keep the old Compose file runnable for one release.
2. Run `optix-worker` against offline fixtures and private databases. Compare its outputs with the old independent workers; never let both write the same production database.
3. Stop old Option Pro workers, start the unified worker and retain the previous release tag and Compose file for rollback.
4. Enable MacroLens ETL-only endpoints. Import the latest visible legacy analysis into Option Pro, then create all new analysis locally.
5. Observe one full United States trading week before deleting the legacy runtime and the compatibility adapter.

## Database handling

- Back up every SQLite database before each stage with the SQLite backup API.
- Record schema versions, table counts, SHA-256 checksums, `quick_check`, `integrity_check` and `foreign_key_check`.
- Roll back code and containers, not database files. Additive migrations must leave old rows readable.
- Preserve response identifiers and `submission_outcome_unknown`; never replay an uncertain paid submission.

## Configuration conversion

Run:

```bash
python -m app.tools.migrate_personal_config .env --output-directory config/migrated
```

The command writes `personal.toml`, mode-0600 `secrets.env` and `unmapped-env.json`. Review the unmapped report before enabling the new runtime. The compatibility adapter is removed after the first Personal Edition production release.

## Rollback

Each pull request has a release tag. To roll back, stop the new process, check out the preceding tag, restore its Compose definition and start it against the same forward-compatible databases. Do not restore an older database over newer task history.
