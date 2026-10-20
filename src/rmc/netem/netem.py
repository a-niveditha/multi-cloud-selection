"""Network conditions (natural / mild / severe) applied to ONE simulated provider path.

This plays the role Linux tc/netem would play on a real path: it adds RTT (a fraction
of the provider's natural RTT) and packet loss to the degraded provider's MockProvider,
and clears it again at the end of the block.
"""
from __future__ import annotations


class MockNetem:
    """Degrades MockProvider objects the way netem would degrade a real path."""

    def __init__(self, mocks: dict):
        self.mocks = mocks
        self.log: list[tuple] = []

    def apply(self, provider: str, rtt_increase_ratio: float, loss_pct: float) -> None:
        self.log.append(("apply", provider, rtt_increase_ratio, loss_pct))
        self.mocks[provider].degrade(rtt_increase_ratio, loss_pct)

    def clear(self) -> None:
        self.log.append(("clear",))
        for m in self.mocks.values():
            m.clear_degradation()

    def has_rules(self) -> bool:
        return any(m.rtt_ms != m.base.rtt_ms or m.loss_pct != m.base.loss_pct for m in self.mocks.values())
