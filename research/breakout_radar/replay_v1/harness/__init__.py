"""Production-faithful replay of the Breakout Radar on frozen Massive data.

Everything after discovery runs the production code (``BreakoutWorker``,
``BreakoutRadarService``, ``BreakoutRepository`` and their adapters' helpers).
Discovery is the one proxied stage: ``discovery.ReplayDiscoveryProvider`` recomputes
the TradingView filters from 5-minute bars and is labelled ``replay_proxy`` in every
snapshot, candidate and event it produces.
"""
