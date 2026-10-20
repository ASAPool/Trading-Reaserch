"""Minimal run metrics needed by DQP-1 (Phase 5). Full statistics come later.

Units (same as DQP-1):
- total_return  R: final equity / starting equity - 1 (fraction)
- expectancy    E: mean closed-trade return, net P&L / entry value (fraction);
                   None (undefined) when there are no closed trades
- max_drawdown  D: largest fall from a running peak, as a positive fraction of
                   that peak; the starting equity is the first peak
- trade_count   N: number of closed trades

COSTS NOT MODELLED (Phase 7): every P&L here is before any trading costs.
"""

from dataclasses import dataclass

COSTS_LABEL = "COSTS NOT MODELLED (Phase 7)"


@dataclass(frozen=True)
class Metrics:
    total_return: float
    expectancy: object        # float, or None when there are no closed trades
    max_drawdown: float
    trade_count: int
    costs: str = COSTS_LABEL


def max_drawdown(starting_equity: float, equity_values) -> float:
    peak, worst = starting_equity, 0.0
    for e in equity_values:
        if e > peak:
            peak = e
        worst = max(worst, (peak - e) / peak)
    return worst


def compute_metrics(starting_equity: float, equity_values, trade_returns) -> Metrics:
    if starting_equity <= 0:
        raise ValueError("starting_equity must be positive")
    equity_values = list(equity_values)
    trade_returns = list(trade_returns)
    final = equity_values[-1] if equity_values else starting_equity
    return Metrics(
        total_return=final / starting_equity - 1,
        expectancy=sum(trade_returns) / len(trade_returns) if trade_returns else None,
        max_drawdown=max_drawdown(starting_equity, equity_values),
        trade_count=len(trade_returns),
    )
