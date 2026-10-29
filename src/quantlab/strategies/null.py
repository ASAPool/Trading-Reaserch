"""null: never trades. A baseline proving the plumbing (zero trades, flat
equity at the starting value, labels present). TEST STRATEGY ONLY."""

from quantlab.strategies.base import VerificationStrategy


class Null(VerificationStrategy):
    NAME = "null"
    VERSION = "1.0.0"

    def __init__(self):
        self.settings = {}

    def on_bar(self, view, position):
        return None
