# Screener gate candidates (research only)

Independent stock-gate candidates `B0/G1/G2/G3`. Not wired into live jobs.
Not a claimed profitable patch.

```bash
PYTHONPATH=backend python -m pytest -q tests/test_screener_gate_candidates_v1.py tests/test_screener_gate_replay_adapter.py tests/test_screener_gate_review_regressions.py
```

Market replay writes outside the repo:

```bash
export SCREENER_GATE_RESEARCH_DIR="$HOME/optix-research/screener-gate-v1"
PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py discover
PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py download --start 2016-01-01 --end 2024-06-28
PYTHONPATH=backend python -u scripts/research/screener_gate_replay_v1.py replay --start 2022-01-03 --end 2024-03-28 --other-variants --extra-sessions 3 --broad-slices 2024-06-28,2023-12-29,2022-12-30 --align-sessions 5
PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py finalize --refresh-examples
```

Holdout from `2024-07-01` stays sealed. Compact results stay outside Git.
`replay` and `finalize` refuse to overwrite an existing `return_pack/` unless `--allow-overwrite` is set. Finalize keeps the scoring-run code hash separate from the summary-tool hash.

Relabel the frozen technical Top-K without rescoring:

```bash
PYTHONPATH=backend python scripts/research/screener_gate_statistics_v2.py \
  --research-dir "$HOME/optix-research/screener-gate-v1" --reps 2000
```

That writes `return_pack_metrics_v2/` and refuses to overwrite it. Discovery checkpoints, if recomputed, go to `return_pack_discovery_v2/` and are not mixed into the old pack.
