"""Market-neutral building blocks.

Will contain:
- the standard price-bar format every market is converted into
- the backtest engine and reference indicators (Phase 5, built)
- the interface for trading costs (Phase 7)

RULE: nothing in this folder may import from ``quantlab.markets``.
The core must work the same for crypto now and for futures later.
"""
