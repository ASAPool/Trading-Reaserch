"""Shared helpers for the Phase 5 tests. MADE-UP data only: every bar here is
invented for testing; no market data is read anywhere in these tests.

With the default fixed notional of 1,000 USD, an entry at 100 buys exactly
10 units, at 125 exactly 8, at 200 exactly 5 (easy hand arithmetic).
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quantlab.core.bars import Bar  # noqa: E402
from quantlab.core.engine import (  # noqa: E402
    BacktestConfig, SyntheticDataset, run_synthetic,
)
from quantlab.core.identity import StrategyIdentity  # noqa: E402
from quantlab.core.policy import load_policy  # noqa: E402

POLICY = load_policy(ROOT / "config" / "data_quality_policy.toml")
BASE = datetime(2030, 1, 1, tzinfo=timezone.utc)   # a made-up future date
H = 3600


def T(h: int) -> datetime:
    return BASE + timedelta(hours=h)


def U(h: int) -> int:
    return int(T(h).timestamp())


def bar(h, o, hi, lo, c) -> Bar:
    return Bar(T(h), H, float(o), float(hi), float(lo), float(c), 1.0)


def flat(h, p) -> Bar:
    return bar(h, p, p, p, p)


def mirror_bar(b: Bar, c: float) -> Bar:
    """Price path reflected around c/2: every price p becomes c - p."""
    return Bar(b.open_time, b.interval_seconds, c - b.open, c - b.low, c - b.high,
               c - b.close, b.volume)


class Script:
    """Returns pre-written orders keyed by the HOUR of the decision bar, and
    records what it was shown (to prove it never sees an unclosed bar)."""

    def __init__(self, orders):
        self.orders = dict(orders)
        self.calls = []        # (latest visible bar time, number of visible bars, position)

    def on_bar(self, view, position):
        latest = view.latest
        self.calls.append((latest.open_time, len(view), position))
        h = int((latest.open_time - BASE).total_seconds() // H)
        order = self.orders.get(h)
        return order(position) if callable(order) else order


TEST_IDENTITY = StrategyIdentity("unit-test strategy", "test", {},
                                 "ENGINE VERIFICATION ONLY - NOT EVIDENCE OF PROFITABILITY")


def identified(factory):
    """Give ad-hoc test strategies (which have no identity) a fixed test identity."""
    def make():
        s = factory()
        if not hasattr(s, "identity"):
            s.identity = TEST_IDENTITY
        return s
    return make


def run(bars, orders=None, notional=1000, flagged=None, strategy=None):
    strategy_factory = identified(strategy or (lambda: Script(orders or {})))
    return run_synthetic(SyntheticDataset(tuple(bars), "unit test", flagged),
                         strategy_factory, BacktestConfig(notional), POLICY)


def run_a(bars, orders=None, **kw):
    return run(bars, orders, **kw).variant_a
