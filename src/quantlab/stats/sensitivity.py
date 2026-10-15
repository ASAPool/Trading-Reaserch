"""DQP-1 comparison rules: data-quality sensitivity and gap-exposure diagnostics.

Two SEPARATE checks, both always reported:

1. compare_variants(): the official Variant A vs Variant B data-quality
   sensitivity test (two real runs of the engine).

2. gap_exposure_diagnostic(): a DIAGNOSTIC CONTRIBUTION ANALYSIS of the
   recorded gap_exposed trades of ONE run. It never computes an alternative
   equity curve, total return, Sharpe ratio or drawdown by deleting trades,
   because with equity-dependent sizing that path would never have happened.

All thresholds come from the policy; nothing here is tunable at call time.
"""

from dataclasses import dataclass, field

from quantlab.core.policy import DataQualityPolicy, SensitivityRule


def classify_sign(value: float, zero_tolerance: float) -> int:
    """+1 positive, -1 negative, 0 zero, using the policy's fixed tolerance.

    Only for DIMENSIONLESS values (returns, expectancy, P&L / starting equity).
    """
    if value != value:  # NaN
        raise ValueError("cannot classify the sign of NaN")
    if value > zero_tolerance:
        return 1
    if value < -zero_tolerance:
        return -1
    return 0


def materially_different(a: float, b: float, rule: SensitivityRule) -> bool:
    """|a - b| > max(floor, relative * max(|a|, |b|))."""
    return abs(a - b) > max(rule.floor, rule.relative * max(abs(a), abs(b)))


@dataclass(frozen=True)
class RunMetrics:
    total_return: float   # fraction (0.12 = +12%)
    expectancy: float     # mean net trade return, fraction of position value
    max_drawdown: float   # positive fraction of peak equity
    trade_count: int


@dataclass
class SensitivityResult:
    sensitive: bool
    reasons: list = field(default_factory=list)
    label: str = ""


def compare_variants(a: RunMetrics, b: RunMetrics, policy: DataQualityPolicy) -> SensitivityResult:
    """Official Variant A (a) vs Variant B (b) data-quality sensitivity test."""
    tol = policy.zero_tolerance
    reasons = []
    for name, va, vb in (("total return", a.total_return, b.total_return),
                         ("expectancy", a.expectancy, b.expectancy)):
        if classify_sign(va, tol) != classify_sign(vb, tol):
            reasons.append(f"sign of {name} changed ({va:+.6g} vs {vb:+.6g})")
    for name, va, vb, rule in (("total return", a.total_return, b.total_return, policy.total_return),
                               ("expectancy", a.expectancy, b.expectancy, policy.expectancy),
                               ("max drawdown", a.max_drawdown, b.max_drawdown, policy.max_drawdown),
                               ("trade count", a.trade_count, b.trade_count, policy.trade_count)):
        if materially_different(va, vb, rule):
            limit = max(rule.floor, rule.relative * max(abs(va), abs(vb)))
            reasons.append(f"{name} differs by {abs(va - vb):.6g} (limit {limit:.6g})")
    sensitive = bool(reasons)
    return SensitivityResult(sensitive, reasons,
                             "SENSITIVE TO DATA QUALITY" if sensitive else "NOT SENSITIVE TO DATA QUALITY")


@dataclass(frozen=True)
class TradeRecord:
    net_pnl: float        # USD, after costs
    entry_value: float    # USD position value at entry (> 0)
    gap_exposed: bool

    @property
    def trade_return(self) -> float:
        return self.net_pnl / self.entry_value


def _mean(values):
    return sum(values) / len(values) if values else None


def gap_exposure_diagnostic(trades: list[TradeRecord], starting_equity: float,
                            policy: DataQualityPolicy) -> dict:
    """DIAGNOSTIC CONTRIBUTION ANALYSIS of gap_exposed trades (recorded values only)."""
    if starting_equity <= 0:
        raise ValueError("starting_equity must be positive")
    for t in trades:
        if t.entry_value <= 0:
            raise ValueError("every trade needs a positive entry_value")
    tol, s0 = policy.zero_tolerance, starting_equity
    gap = [t for t in trades if t.gap_exposed]
    non = [t for t in trades if not t.gap_exposed]
    p_all = sum(t.net_pnl for t in trades)
    p_gap = sum(t.net_pnl for t in gap)
    e_all = _mean([t.trade_return for t in trades])
    e_gap = _mean([t.trade_return for t in gap])
    e_non = _mean([t.trade_return for t in non])
    sign_all = classify_sign(p_all / s0, tol)
    sign_without = classify_sign((p_all - p_gap) / s0, tol)

    reasons = []
    if gap:
        if sign_without != sign_all:
            reasons.append("rule 1: subtracting their recorded P&L contribution changes the sign "
                           "of overall recorded net P&L")
        if e_non is None:
            reasons.append("rule 2: every trade is gap_exposed, so non-gap expectancy is undefined")
        elif classify_sign(e_non, tol) != classify_sign(e_all, tol):
            reasons.append("rule 2: expectancy of non-gap-exposed trades has a different sign "
                           "than expectancy of all trades")
        limit = max(policy.gap_contribution.floor * s0, policy.gap_contribution.relative * abs(p_all))
        if abs(p_gap) > limit:
            reasons.append(f"rule 3: |gap-exposed P&L| {abs(p_gap):.2f} exceeds the limit {limit:.2f}")
        if e_non is not None and materially_different(e_all, e_non, policy.gap_expectancy):
            reasons.append("rule 4: expectancy of all trades and of non-gap-exposed trades "
                           "differ materially")
    sensitive = bool(reasons)
    return {
        "analysis": "DIAGNOSTIC CONTRIBUTION ANALYSIS (recorded trades only; not an alternative "
                    "backtest, equity curve, total return, Sharpe ratio or drawdown)",
        "gap_exposed_trades": len(gap),
        "gap_exposed_net_pnl": p_gap,
        "gap_exposed_net_pnl_fraction_of_starting_equity": p_gap / s0,
        "gap_exposed_share_of_total_net_pnl": (p_gap / p_all) if sign_all != 0 else None,
        "gap_exposed_expectancy": e_gap,
        "all_trades_expectancy": e_all,
        "non_gap_exposed_expectancy": e_non,
        "total_recorded_net_pnl": p_all,
        "subtracting_contribution_changes_net_pnl_sign": bool(gap) and sign_without != sign_all,
        "verdict": "GAP-EXPOSURE SENSITIVE" if sensitive else
                   ("NO GAP EXPOSURE" if not gap else "GAP EXPOSURE NOT MATERIAL"),
        "auditor_review_required": sensitive,
        "reasons": reasons,
    }
