# Screener gate candidates (research only)

Independent stock-gate candidates `B0/G1/G2/G3`. Not wired into live jobs.
Not a claimed profitable patch.

```bash
PYTHONPATH=backend python -m pytest -q tests/test_screener_gate_candidates_v1.py
PYTHONPATH=backend python -m pytest -q tests/test_screener_gate_replay_adapter.py
```

Market replay writes outside the repo:

```bash
export SCREENER_GATE_RESEARCH_DIR="$HOME/optix-research/screener-gate-v1"
PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py discover
PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py download --start 2016-01-01 --end 2024-06-28
PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py replay --start 2023-01-03 --end 2024-03-28 --other-variants --broad-slices 2024-06-28,2023-12-29,2022-12-30
```

Holdout from `2024-07-01` stays sealed. Compact results stay in the research directory, not in Git.
