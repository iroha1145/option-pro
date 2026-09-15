# Round 2 review artifacts

These files are the reviewable packet for the second correctness pass.
Full event/ledger dumps stay outside Git. Hashes for both the compact
copies and the external files are in `artifact-hashes.json`.

Reproduce:

```bash
PYTHONPATH=backend python -m pytest tests/test_research_review_round2.py
PYTHONPATH=backend python -m pytest tests/test_research_screener_radar.py
PYTHONPATH=backend python scripts/research/recompute_round2.py
```

Dataset identity: `yahoo-daily` `content_sha256=09be8d4a23ca1b4822b41798e1f06d2d6da2d1ae3eca6c1c56777fba8bcc9ec7`.
Pre-repair dumps were marked with `*.PENDING_REVIEW.json` sidecars and were not overwritten.
