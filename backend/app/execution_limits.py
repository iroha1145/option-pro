"""Shared upper bounds for operations that can stall the worker event loop."""

BREAKOUT_TASK_TIMEOUT_SECONDS = 900.0

# The worker supervisor cancels a whole ai_jobs pass after this long, taking
# every slot of that pass with it. It has to outlast the longest paid wait
# (Settings.openai_background_poll_timeout_seconds, 3600 by default).
AI_JOBS_TASK_TIMEOUT_SECONDS = 3900.0
# A paid Claude stream gives up this long before the supervisor would cancel
# the pass, so its own failure is recorded and the other slots keep running.
AI_JOBS_SUPERVISOR_MARGIN_SECONDS = 300.0


__all__ = [
    "AI_JOBS_SUPERVISOR_MARGIN_SECONDS",
    "AI_JOBS_TASK_TIMEOUT_SECONDS",
    "BREAKOUT_TASK_TIMEOUT_SECONDS",
]
