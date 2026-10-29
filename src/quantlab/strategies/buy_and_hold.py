"""buy_and_hold: enter at the first possible bar, never exit. TEST STRATEGY ONLY.

Rule (LONG; SHORT is the mirror): at the close of every bar while flat,
request a market entry with no stop and no target. The first such request
fills at the next open; if that next bar is missing, the request expires and
the strategy simply asks again at the next bar it sees. Once in a position it
never asks to exit (an open position at the end of the data is reported, not
closed).
"""

from quantlab.core.engine import Entry
from quantlab.strategies.base import VerificationStrategy, check_side


class BuyAndHold(VerificationStrategy):
    NAME = "buy_and_hold"
    VERSION = "1.0.0"

    def __init__(self, side="LONG"):
        self.side = check_side(side)
        self.settings = {"side": self.side}

    def on_bar(self, view, position):
        return Entry(self.side) if position is None else None
