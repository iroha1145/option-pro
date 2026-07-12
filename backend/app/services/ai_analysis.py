"""Compatibility guard for the retired synchronous AI implementation.

All production model work now lives in app.services.ai_jobs. Keeping these
names makes accidental calls fail with a stable migration code instead of
silently starting an untracked, paid request.
"""

from __future__ import annotations


def _job_required(*_args, **_kwargs):
    raise RuntimeError("ai_job_required")


analyze_option_alerts = _job_required
analyze_signals = _job_required
analyze_earnings_correlation = _job_required
analyze_single_earnings_impact = _job_required
