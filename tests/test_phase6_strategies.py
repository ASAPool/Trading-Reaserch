"""Phase 6: strategy identity, registry, the null and buy_and_hold test
strategies, and the package rules. MADE-UP data only; TEST STRATEGIES only.
"""

import unittest

import phase5_helpers as h5
import phase6_helpers as h6
from phase5_helpers import T, U, flat, mirror_bar
from phase6_helpers import LONG, SHORT, run_recorded
from quantlab.core.engine import (
    BacktestConfig, EngineError, Entry, SyntheticDataset, format_report, run_synthetic,
)
from quantlab.core.identity import StrategyIdentity
from quantlab.strategies.base import PURPOSE, create, registry
from quantlab.strategies.buy_and_hold import BuyAndHold
from quantlab.strategies.channel_breakout import ChannelBreakout
from quantlab.strategies.ma_cross import MACross
from quantlab.strategies.null import Null

MA_CROSS_JSON = (
    '{"name":"ma_cross","purpose":"ENGINE VERIFICATION ONLY - test strategy on synthetic '
    'data; NOT a research strategy; NOT evidence of profitability","settings":{"fast_hours":3,'
    '"side":"LONG","slow_hours":5,"stop_fraction":0.02,"target_fraction":0.04},'
    '"version":"1.0.0"}')
MA_CROSS_SHA256 = "61f4031ffd17e86bbd4a011d5fdb9b2c3ad820160bca9727f9094c726bf06030"


class TestIdentity(unittest.TestCase):

    def test_identity_is_built_from_name_version_and_every_setting(self):
        ident = MACross().identity
        self.assertEqual(ident.canonical_json(), MA_CROSS_JSON)
        self.assertEqual(ident.sha256, MA_CROSS_SHA256)        # same on every computer
        self.assertEqual(ident.as_dict()["settings"], {
            "side": "LONG", "fast_hours": 3, "slow_hours": 5,
            "stop_fraction": 0.02, "target_fraction": 0.04})
        self.assertEqual(ident.purpose, PURPOSE)
        self.assertNotIn("object at", ident.canonical_json())
        self.assertNotIn("MACross", ident.canonical_json())    # not a class name

    def test_same_strategy_same_identity_different_settings_different_identity(self):
        self.assertEqual(MACross().identity, MACross().identity)
        self.assertEqual(MACross().identity.sha256, create("ma_cross").identity.sha256)
        shas = {MACross().identity.sha256, MACross(side=SHORT).identity.sha256,
                ChannelBreakout().identity.sha256, ChannelBreakout(hold_hours=4).identity.sha256,
                BuyAndHold().identity.sha256, BuyAndHold(SHORT).identity.sha256,
                Null().identity.sha256}
        self.assertEqual(len(shas), 7)

    def test_setting_order_does_not_matter_and_bad_values_are_refused(self):
        a = StrategyIdentity("x", "1", {"a": 1, "b": 2.5})
        b = StrategyIdentity("x", "1", {"b": 2.5, "a": 1})
        self.assertEqual((a, a.sha256), (b, b.sha256))
        for bad in ({"a": float("nan")}, {"a": [1]}, {"a": object()}, {1: 2}):
            with self.subTest(bad), self.assertRaises(ValueError):
                StrategyIdentity("x", "1", bad)
        for name, version in (("", "1"), ("x", ""), ("x", None)):
            with self.assertRaises(ValueError):
                StrategyIdentity(name, version, {})

    def test_every_result_records_the_identity(self):
        bars = [flat(i, 100) for i in range(8)]
        r = run_recorded(bars, ChannelBreakout)[0]
        self.assertEqual(r.strategy_identity, ChannelBreakout().identity.as_dict())
        self.assertEqual(r.strategy_identity["version"], "1.0.0")
        text = format_report(r)
        self.assertIn("Strategy: channel_breakout v1.0.0", text)
        self.assertIn("NOT evidence of profitability", text)
        self.assertTrue(text.startswith("COSTS NOT MODELLED (Phase 7)"))

    def test_engine_refuses_strategies_without_or_with_unstable_identity(self):
        data = SyntheticDataset((flat(0, 100),), "unit test")

        class Anonymous:
            def on_bar(self, view, position):
                return None
        with self.assertRaises(EngineError):
            run_synthetic(data, Anonymous, BacktestConfig(1000), h5.POLICY)
        sides = iter((LONG, SHORT))
        with self.assertRaises(EngineError):
            run_synthetic(data, lambda: BuyAndHold(next(sides)), BacktestConfig(1000), h5.POLICY)

    def test_registry(self):
        self.assertEqual(sorted(registry()), ["buy_and_hold", "channel_breakout", "ma_cross", "null"])
        self.assertIsInstance(create("channel_breakout", side=SHORT), ChannelBreakout)
        with self.assertRaises(ValueError):
            create("best_strategy")
        with self.assertRaises(TypeError):
            create("null", side=LONG)


class TestNull(unittest.TestCase):

    def test_never_trades_and_equity_stays_flat(self):
        bars = h6.random_path(1, n=50)
        r, rec, _ = run_recorded(bars, Null)
        for v in (r.variant_a, r.variant_b):
            self.assertEqual((v.trades, v.order_events, v.open_position_at_end), ((), (), None))
            self.assertEqual({e for _, e in v.equity_curve}, {10_000})
            self.assertEqual(v.metrics.trade_count, 0)
        self.assertEqual({o for _, _, o in rec.log}, {None})
        self.assertIn("NOT EVALUABLE", r.sensitivity["label"])


class TestBuyAndHold(unittest.TestCase):

    def test_enters_at_first_possible_bar_and_never_exits(self):
        # Decided at h0, fills at h1 open 100 (10 units). Marked at h3 close 105: +50.
        bars = [flat(0, 100), flat(1, 100), flat(2, 102), flat(3, 105)]
        r, rec, _ = run_recorded(bars, BuyAndHold)
        self.assertEqual([o for _, _, o in rec.log], [Entry(LONG), None, None, None])
        v = r.variant_a
        p = v.open_position_at_end
        self.assertEqual((v.trades, p["entry_time"], p["entry_price"], p["unrealized_pnl"]),
                         ((), T(1), 100, 50))
        self.assertEqual([e for _, e in v.equity_curve], [10_000, 10_000, 10_020, 10_050])

    def test_entry_expires_across_a_gap_then_retries(self):
        bars = [flat(0, 100), flat(2, 100), flat(3, 101), flat(4, 101)]   # h1 missing
        v = run_recorded(bars, BuyAndHold)[0].variant_a
        self.assertEqual([(e.decided_at, e.reason) for e in v.order_events],
                         [(T(0), "next_bar_missing")])
        self.assertEqual(v.open_position_at_end["entry_time"], T(3))

    def test_variant_b_masked_hour_delays_the_entry(self):
        bars = [flat(i, 100) for i in range(4)]
        r = run_recorded(bars, BuyAndHold, flagged={U(1): True})[0]
        self.assertEqual(r.variant_a.open_position_at_end["entry_time"], T(1))
        self.assertEqual(r.variant_b.open_position_at_end["entry_time"], T(3))

    def test_short_is_the_mirror(self):
        bars = h6.random_path(3, n=60)
        a = run_recorded(bars, BuyAndHold)[0].variant_a
        m = run_recorded([mirror_bar(b, h6.MIRROR_C) for b in bars],
                         lambda: BuyAndHold(SHORT))[0].variant_a
        pa, pm = a.open_position_at_end, m.open_position_at_end
        self.assertEqual((pa["side"], pm["side"]), (LONG, SHORT))
        self.assertEqual(pa["entry_time"], pm["entry_time"])
        self.assertEqual(h6.MIRROR_C - pa["entry_price"], pm["entry_price"])
        self.assertAlmostEqual(pa["unrealized_pnl"] / pa["quantity"],
                               pm["unrealized_pnl"] / pm["quantity"], places=9)


class TestPackageRules(unittest.TestCase):

    def test_strategies_cannot_load_data_or_markets(self):
        for py in (h5.ROOT / "src" / "quantlab" / "strategies").glob("*.py"):
            text = py.read_text(encoding="utf-8")
            imports = [ln for ln in text.splitlines() if ln.strip().startswith(("import ", "from "))]
            with self.subTest(py.name):
                for line in imports:
                    self.assertNotIn("quantlab.data", line)
                    self.assertNotIn("quantlab.markets", line)
                for forbidden in ("open(", "Path(", "csv", "run_real_dataset", "tomllib"):
                    self.assertNotIn(forbidden, text)

    def test_oracle_is_independent_of_production_strategy_code(self):
        text = (h5.ROOT / "tests" / "phase6_helpers.py").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith(("import ", "from ")):
                with self.subTest(line):
                    self.assertNotIn("quantlab.strategies", line)
                    self.assertNotIn("indicators", line)

    def test_hour_based_strategies_refuse_non_hourly_bars(self):
        from quantlab.core.bars import Bar
        quarter_hour = [Bar(T(0), 900, 100.0, 100.0, 100.0, 100.0, 1.0)]
        for make in (MACross, ChannelBreakout):
            with self.subTest(make), self.assertRaises(ValueError):
                h5.run(quarter_hour, strategy=make)


if __name__ == "__main__":
    unittest.main()
