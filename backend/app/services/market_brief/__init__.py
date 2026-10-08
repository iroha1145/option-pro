"""首页「市场综合研判」：每个交易日开盘前 / 收盘后各一份，由 Claude Opus 5.5 生成。

独立于 ai_jobs（OpenAI 任务队列）子系统，只复用其简体中文校验函数。
公开接口见各子模块：schema（契约）、scheduler（槽）、evidence（证据包）、
runner（一次运行）、store（落盘与投影）。
"""

from .evidence import BENCHMARK_CODES, EvidencePack, build_evidence, eod_batch_served_session
from .runner import BriefRunConfig, run_brief
from .scheduler import BriefSchedule, due_slot, next_slot_at, post_close_fallback_reached, slot_window
from .schema import SCHEMA_VERSION, BriefSlot, BriefTrigger, MarketBriefResult
from .store import BriefRunRecord, BriefStore

__all__ = [
    "SCHEMA_VERSION",
    "BriefSlot",
    "BriefTrigger",
    "MarketBriefResult",
    "BriefSchedule",
    "due_slot",
    "next_slot_at",
    "post_close_fallback_reached",
    "slot_window",
    "BriefRunConfig",
    "run_brief",
    "BriefRunRecord",
    "BriefStore",
    "BENCHMARK_CODES",
    "EvidencePack",
    "build_evidence",
    "eod_batch_served_session",
]
