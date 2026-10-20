"""The smallest trustworthy backtester (Phase 5). SYNTHETIC DATA ONLY.

Replays bars one at a time. The plan it implements, rule by rule, is
docs/phase5-plan.md; plain-language explanation: docs/backtest-engine.md.

NO LOOK-AHEAD, BY CONSTRUCTION: the strategy is given a HistoryView over a
list the engine appends to only AFTER each bar has closed. Future bars are
never placed anywhere the strategy can reach; reading past the latest closed
bar raises LookAheadError.

Per bar, in this fixed order:
  1. Resolve the order decided at the previous bar's close, at this bar's OPEN
     (entry fill / expiry / bracket cancellation; stop-target adjustment).
  2. Open check for a position held from an earlier bar (DQP-1
     first_bar_after_gap, applied to EVERY bar): open past the stop -> exit at
     the open; open past the target -> exit at the target; otherwise a pending
     market exit fills at the open.
  3. Intrabar: stop and target both touched -> stop assumed first; one touched
     -> fill exactly at that level.
  4. The bar closes: equity = starting equity + realized + unrealized (at close).
  5. Only now does the strategy see this bar and may decide ONE order for the
     next bar.

Missing bars are never filled. During missing hours nothing is executed or
inferred; a position open during any missing hour is gap_exposed.

COSTS NOT MODELLED (Phase 7). One position at a time, fixed notional, no
compounding, no leverage.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from quantlab.core.bars import Bar
from quantlab.core.gap_execution import LONG, SHORT, first_bar_after_gap, is_gap_exposed, segments
from quantlab.core.policy import DataQualityPolicy
from quantlab.stats.metrics import COSTS_LABEL, Metrics, compute_metrics
from quantlab.stats.sensitivity import (
    RunMetrics, TradeRecord, compare_variants, gap_exposure_diagnostic,
)

STARTING_EQUITY = 10_000.0    # locked by the Phase 5 plan (section 5)
REAL_DATA_LOCK_MESSAGE = "REAL DATA LOCKED: no frozen train/validation/final-test split exists"
SYNTHETIC_DATASET = "SYNTHETIC (made-up data, not market history)"


class EngineError(Exception):
    """Bad input or an internal inconsistency. The run stops; nothing is guessed."""


class LookAheadError(EngineError, IndexError):
    """A strategy tried to read a bar that has not closed yet."""


class StrategyError(EngineError):
    """A strategy returned an order the rules do not allow."""


class RealDataLockedError(EngineError):
    """Phase 5 never runs on real market history."""


# ---------------------------------------------------------------- orders ----

@dataclass(frozen=True)
class Entry:
    """Market entry at the next bar's open. stop/target are ABSOLUTE prices
    fixed now, from information at or before the decision bar."""
    side: str
    stop: float = None
    target: float = None


@dataclass(frozen=True)
class Exit:
    """Market exit at the next available bar's open."""


@dataclass(frozen=True)
class Adjust:
    """Replace the position's stop and target (None = no level). Active from
    the following bar, including that bar's open check."""
    stop: float = None
    target: float = None


def _check_price(name, value):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value <= 0:
        raise StrategyError(f"{name} must be a finite positive price or None, got {value!r}")


def _check_levels(side, stop, target):
    _check_price("stop", stop)
    _check_price("target", target)
    if stop is not None and target is not None:
        if side == LONG and not stop < target:
            raise StrategyError("for a LONG position the stop must be below the target")
        if side == SHORT and not stop > target:
            raise StrategyError("for a SHORT position the stop must be above the target")


# ------------------------------------------------------- what is visible ----

class HistoryView:
    """Read-only access to CLOSED bars only (oldest first)."""

    def __init__(self, bars: list, missing_before: list, interval: int):
        self._bars, self._missing, self.interval = bars, missing_before, interval

    def __len__(self):
        return len(self._bars)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return tuple(self._bars[i])
        n = len(self._bars)
        if i >= n:
            raise LookAheadError(f"bar {i} has not closed yet (only {n} closed bars are visible)")
        if i < -n:
            raise IndexError(f"bar {i} is before the first bar")
        return self._bars[i]

    @property
    def latest(self) -> Bar:
        return self[-1]

    def missing_before(self, i) -> int:
        """Number of missing bars immediately before visible bar i."""
        self[i]
        return self._missing[i]


@dataclass(frozen=True)
class PositionView:
    side: str
    quantity: float
    entry_time: datetime
    entry_price: float
    stop: float
    target: float
    gap_exposed: bool


# --------------------------------------------------------------- results ----

@dataclass(frozen=True)
class Trade:
    side: str
    quantity: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime          # open time of the bar in which the exit filled
    exit_price: float
    exit_reason: str
    gap_exposed: bool
    touched_flagged_bar: bool
    entry_value: float           # = fixed notional
    net_pnl: float               # USD; COSTS NOT MODELLED (Phase 7)

    @property
    def trade_return(self) -> float:
        return self.net_pnl / self.entry_value


@dataclass(frozen=True)
class OrderEvent:
    kind: str                    # "entry_expired" or "entry_cancelled"
    side: str
    decided_at: datetime         # open time of the bar whose close produced the decision
    resolved_at: object          # datetime, or None at the end of the data
    reason: str                  # "next_bar_missing", "end_of_data", "invalid_bracket_at_fill"


@dataclass(frozen=True)
class VariantResult:
    variant: str
    trades: tuple
    equity_curve: tuple          # (bar close time, equity) at every available bar close
    order_events: tuple
    decisions: tuple             # (decision bar open time, order or None) for every bar
    open_position_at_end: object # dict, or None
    missing_times: tuple         # bar slots treated as missing in this variant (Unix seconds)
    metrics: Metrics
    gap_diagnostic: dict
    costs: str = COSTS_LABEL


@dataclass(frozen=True)
class BacktestResult:
    variant_a: VariantResult     # OFFICIAL
    variant_b: VariantResult
    sensitivity: dict
    data_identity: dict
    official: str = "A"
    costs: str = COSTS_LABEL


@dataclass(frozen=True)
class BacktestConfig:
    fixed_notional: float
    starting_equity: float = STARTING_EQUITY

    def __post_init__(self):
        if self.starting_equity != STARTING_EQUITY:
            raise EngineError(f"starting_equity is locked at {STARTING_EQUITY:g} in Phase 5")
        n = self.fixed_notional
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) \
                or not 0 < n <= self.starting_equity:
            raise EngineError("fixed_notional must satisfy 0 < fixed_notional <= starting_equity "
                              "(no leverage)")

    @classmethod
    def from_settings(cls, settings: dict) -> "BacktestConfig":
        try:
            b = settings["backtest"]
            return cls(fixed_notional=b["fixed_notional"], starting_equity=b["starting_equity"])
        except (KeyError, TypeError) as e:
            raise EngineError(f"settings have no complete [backtest] section: {e!r}") from None


@dataclass(frozen=True)
class SyntheticDataset:
    """Made-up bars for testing the engine. `flagged_hours` maps a bar's open
    time (Unix seconds) to whether Variant B treats it as missing."""
    bars: tuple
    label: str
    flagged_hours: dict = None


# ----------------------------------------------------------------- engine ---

def _unix(dt: datetime) -> int:
    return int(dt.timestamp())


def _check_bars(bars) -> int:
    if not bars:
        raise EngineError("no bars")
    interval = bars[0].interval_seconds
    for i, b in enumerate(bars):
        if not isinstance(b, Bar):
            raise EngineError(f"item {i} is not a Bar")
        if b.interval_seconds != interval:
            raise EngineError("all bars must have the same interval")
        prices = (b.open, b.high, b.low, b.close)
        if not all(isinstance(p, (int, float)) and math.isfinite(p) and p > 0 for p in prices):
            raise EngineError(f"bar {b.open_time} has a non-finite or non-positive price")
        if not (b.low <= min(b.open, b.close) and b.high >= max(b.open, b.close)):
            raise EngineError(f"bar {b.open_time} is an impossible candle")
        if _unix(b.open_time) % interval:
            raise EngineError(f"bar {b.open_time} is not on a bar boundary")
        if i and b.open_time <= bars[i - 1].open_time:
            raise EngineError(f"bar {b.open_time} is out of order or duplicated")
    return interval


def _pnl(side, quantity, entry, exit_price):
    return quantity * (exit_price - entry) if side == LONG else quantity * (entry - exit_price)


def run_variant(bars, strategy, config: BacktestConfig, policy: DataQualityPolicy,
                variant: str = "A", flagged_times=frozenset(), masked_times=frozenset()) -> VariantResult:
    """One replay. Bars whose open time is in `masked_times` are treated
    exactly like missing bars (Variant B)."""
    bars = list(bars)
    interval = _check_bars(bars)
    bars = [b for b in bars if _unix(b.open_time) not in masked_times]
    if not bars:
        raise EngineError("every bar is masked")
    times = [_unix(b.open_time) for b in bars]
    missing_before = [s["missing_before"] for s in segments(times, interval)]
    present = set(times)
    missing_times = tuple(t for t in range(times[0], times[-1], interval) if t not in present)
    flagged_times = frozenset(flagged_times)
    s0, notional = config.starting_equity, config.fixed_notional

    seen, seen_missing = [], []
    view = HistoryView(seen, seen_missing, interval)
    trades, events, decisions, equity = [], [], [], []
    realized = 0.0
    pos = None          # dict while a position is open
    pending = None      # (order, decided_at_unix, decided_at_datetime)

    def close_position(i, price, reason):
        nonlocal pos, realized
        pnl = _pnl(pos["side"], pos["quantity"], pos["entry_price"], price)
        exposed = is_gap_exposed(times[pos["index"]], times[i], missing_times, interval)
        if exposed != pos["gap_exposed"]:
            raise EngineError("internal inconsistency: gap_exposed tracking disagrees with "
                              "is_gap_exposed()")
        realized += pnl
        trades.append(Trade(
            side=pos["side"], quantity=pos["quantity"], entry_time=bars[pos["index"]].open_time,
            entry_price=pos["entry_price"], exit_time=bars[i].open_time, exit_price=price,
            exit_reason=reason, gap_exposed=exposed,
            touched_flagged_bar=any(times[k] in flagged_times for k in range(pos["index"], i + 1)),
            entry_value=notional, net_pnl=pnl))
        pos = None

    for i, bar in enumerate(bars):
        t, gap = times[i], missing_before[i] > 0
        if pos is not None and gap:
            pos["gap_exposed"] = True     # open during missing hours: nothing executed there

        # 1. resolve the order decided at the previous bar's close, at this open
        just_entered, pending_exit = False, False
        if pending is not None:
            order, decided_unix, decided_dt = pending
            pending = None
            if isinstance(order, Entry):
                if t - decided_unix != interval:
                    events.append(OrderEvent("entry_expired", order.side, decided_dt,
                                             bar.open_time, "next_bar_missing"))
                else:
                    fill = bar.open
                    if order.side == LONG:
                        bad = (order.stop is not None and order.stop >= fill) or \
                              (order.target is not None and order.target <= fill)
                    else:
                        bad = (order.stop is not None and order.stop <= fill) or \
                              (order.target is not None and order.target >= fill)
                    if bad:
                        events.append(OrderEvent("entry_cancelled", order.side, decided_dt,
                                                 bar.open_time, "invalid_bracket_at_fill"))
                    else:
                        pos = {"side": order.side, "quantity": notional / fill, "index": i,
                               "entry_price": fill, "stop": order.stop, "target": order.target,
                               "gap_exposed": False}
                        just_entered = True
            elif isinstance(order, Adjust):
                pos["stop"], pos["target"] = order.stop, order.target
            else:
                pending_exit = True

        # 2. open check for a position held from an earlier bar (every bar)
        if pos is not None and not just_entered:
            d = first_bar_after_gap(pos["side"], bar.open, pos["stop"], pos["target"])
            if d.action == "exit_stop":
                close_position(i, d.price, "stop_after_gap" if gap else "stop_at_open")
            elif d.action == "exit_target":
                close_position(i, d.price, "target_after_gap" if gap else "target_at_open")
            elif pending_exit:
                close_position(i, bar.open, "exit_after_gap" if gap else "market_exit")

        # 3. intrabar: stop first if both levels are inside the bar
        if pos is not None:
            stop, target = pos["stop"], pos["target"]
            if pos["side"] == LONG:
                stop_hit = stop is not None and bar.low <= stop
                target_hit = target is not None and bar.high >= target
            else:
                stop_hit = stop is not None and bar.high >= stop
                target_hit = target is not None and bar.low <= target
            if stop_hit:
                close_position(i, stop, "stop_and_target_in_bar_stop_assumed" if target_hit
                               else "stop_intrabar")
            elif target_hit:
                close_position(i, target, "target_intrabar")

        # 4. the bar closes: mark to market
        unrealized = 0.0 if pos is None else _pnl(pos["side"], pos["quantity"],
                                                  pos["entry_price"], bar.close)
        equity.append((bar.close_time, s0 + realized + unrealized))

        # 5. only now may the strategy see this bar and decide for the next one
        seen.append(bar)
        seen_missing.append(missing_before[i])
        pview = None if pos is None else PositionView(
            pos["side"], pos["quantity"], bars[pos["index"]].open_time, pos["entry_price"],
            pos["stop"], pos["target"], pos["gap_exposed"])
        order = strategy.on_bar(view, pview)
        if order is not None:
            if isinstance(order, Entry):
                if pos is not None:
                    raise StrategyError("Entry while a position is open (one position at a time)")
                if order.side not in (LONG, SHORT):
                    raise StrategyError(f"side must be {LONG!r} or {SHORT!r}")
                _check_levels(order.side, order.stop, order.target)
            elif isinstance(order, (Exit, Adjust)):
                if pos is None:
                    raise StrategyError(f"{type(order).__name__} without an open position")
                if isinstance(order, Adjust):
                    _check_levels(pos["side"], order.stop, order.target)
            else:
                raise StrategyError(f"unknown order {order!r}")
            pending = (order, t, bar.open_time)
        decisions.append((bar.open_time, order))

    open_at_end = None
    if pending is not None and isinstance(pending[0], Entry):
        events.append(OrderEvent("entry_expired", pending[0].side, pending[2], None, "end_of_data"))
    if pos is not None:
        last = bars[-1]
        open_at_end = {
            "note": "position still open at the end of the data; NOT closed, no fill invented",
            "side": pos["side"], "quantity": pos["quantity"],
            "entry_time": bars[pos["index"]].open_time, "entry_price": pos["entry_price"],
            "stop": pos["stop"], "target": pos["target"], "gap_exposed": pos["gap_exposed"],
            "marked_at_close_of": last.open_time, "mark_price": last.close,
            "unrealized_pnl": _pnl(pos["side"], pos["quantity"], pos["entry_price"], last.close),
            "exit_requested_unfilled": pending is not None and isinstance(pending[0], Exit),
        }

    metrics = compute_metrics(s0, [e for _, e in equity], [tr.trade_return for tr in trades])
    diag = gap_exposure_diagnostic(
        [TradeRecord(tr.net_pnl, tr.entry_value, tr.gap_exposed) for tr in trades], s0, policy)
    return VariantResult(variant, tuple(trades), tuple(equity), tuple(events), tuple(decisions),
                         open_at_end, missing_times, metrics, diag)


def _sensitivity(a: Metrics, b: Metrics, policy: DataQualityPolicy) -> dict:
    if a.expectancy is None or b.expectancy is None:
        return {"label": "SENSITIVITY NOT EVALUABLE: expectancy undefined (no closed trades in "
                         "at least one variant)",
                "sensitive": None, "auditor_review_required": True, "reasons": []}
    r = compare_variants(RunMetrics(a.total_return, a.expectancy, a.max_drawdown, a.trade_count),
                         RunMetrics(b.total_return, b.expectancy, b.max_drawdown, b.trade_count),
                         policy)
    return {"label": r.label, "sensitive": r.sensitive, "auditor_review_required": r.sensitive,
            "reasons": list(r.reasons)}


def run_synthetic(dataset: SyntheticDataset, strategy_factory, config: BacktestConfig,
                  policy: DataQualityPolicy) -> BacktestResult:
    """The ONLY entry point that runs in Phase 5: made-up data only.

    Runs Variant A (official: every bar) and Variant B (Variant-B-masked hours
    treated exactly like missing hours), each with a fresh strategy instance.
    """
    if not isinstance(dataset, SyntheticDataset):
        raise RealDataLockedError(REAL_DATA_LOCK_MESSAGE + " (run_synthetic needs a SyntheticDataset)")
    flags = dict(dataset.flagged_hours or {})
    masked = frozenset(t for t, m in flags.items() if m)
    a = run_variant(dataset.bars, strategy_factory(), config, policy, "A", frozenset(flags))
    b = run_variant(dataset.bars, strategy_factory(), config, policy, "B", frozenset(flags), masked)
    identity = {"dataset": SYNTHETIC_DATASET, "label": dataset.label,
                "policy_version": policy.version}
    return BacktestResult(a, b, _sensitivity(a.metrics, b.metrics, policy), identity)


def run_real_dataset(*args, **kwargs):
    """Real-dataset entry point. In Phase 5 it ALWAYS refuses: only the future
    split-freeze phase may unlock it, and only for train/validation periods."""
    raise RealDataLockedError(REAL_DATA_LOCK_MESSAGE)


def format_report(result: BacktestResult) -> str:
    """Short human-readable summary. The costs label always comes first."""
    lines = [result.costs, f"Dataset: {result.data_identity['dataset']} - "
                           f"{result.data_identity['label']}"]
    for v in (result.variant_a, result.variant_b):
        m = v.metrics
        e = "undefined" if m.expectancy is None else f"{m.expectancy:+.6f}"
        lines.append(f"Variant {v.variant}{' (OFFICIAL)' if v.variant == result.official else ''}: "
                     f"trades {m.trade_count}, total return {m.total_return:+.6f}, "
                     f"expectancy {e}, max drawdown {m.max_drawdown:.6f}, "
                     f"gap-exposure: {v.gap_diagnostic['verdict']}")
    lines.append(f"Data-quality sensitivity: {result.sensitivity['label']}")
    lines.append(result.costs)
    return "\n".join(lines)
