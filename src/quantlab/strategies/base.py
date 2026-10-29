"""Shared pieces of the Phase 6 test strategies: side checks, setting checks,
the identity every strategy carries, and the registry of strategy names.

Each strategy has a NAME, an explicit VERSION, and frozen settings validated
when it is created. Its identity is built from exactly those values.
"""

import math

from quantlab.core.gap_execution import LONG, SHORT
from quantlab.core.identity import StrategyIdentity

PURPOSE = ("ENGINE VERIFICATION ONLY - test strategy on synthetic data; "
           "NOT a research strategy; NOT evidence of profitability")
HOUR = 3600


def check_side(side):
    if side not in (LONG, SHORT):
        raise ValueError(f"side must be {LONG!r} or {SHORT!r}, got {side!r}")
    return side


def check_positive_int(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive whole number, got {value!r}")
    return value


def check_fraction(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or not 0 < value < 1:
        raise ValueError(f"{name} must be a number strictly between 0 and 1, got {value!r}")
    return float(value)


def check_hourly(view):
    if view.interval != HOUR:
        raise ValueError("Phase 6 test strategies are defined for 1-hour bars only")


class VerificationStrategy:
    """Base class. Subclasses set NAME, VERSION and self.settings (a dict)."""
    NAME = None
    VERSION = None

    @property
    def identity(self) -> StrategyIdentity:
        return StrategyIdentity(self.NAME, self.VERSION, dict(self.settings), PURPOSE)

    def on_bar(self, view, position):
        raise NotImplementedError


def registry() -> dict:
    from quantlab.strategies.buy_and_hold import BuyAndHold
    from quantlab.strategies.channel_breakout import ChannelBreakout
    from quantlab.strategies.ma_cross import MACross
    from quantlab.strategies.null import Null
    return {cls.NAME: cls for cls in (Null, BuyAndHold, MACross, ChannelBreakout)}


def create(name: str, **settings):
    """Build a strategy by its registered name. Unknown names are refused."""
    reg = registry()
    if name not in reg:
        raise ValueError(f"unknown strategy {name!r}; known: {sorted(reg)}")
    return reg[name](**settings)
