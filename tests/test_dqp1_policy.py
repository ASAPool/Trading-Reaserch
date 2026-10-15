"""DQP-1 tests: policy file, versioning, zero tolerance, sensitivity rules,
gap-exposure diagnostic and the symmetric first-bar-after-gap execution rule.
All numbers here are made up; no market data is used."""

import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quantlab.core.gap_execution import (  # noqa: E402
    LONG, SHORT, first_bar_after_gap, is_gap_exposed, one_bar_returns, segments,
)
from quantlab.core.policy import (  # noqa: E402
    PolicyError, canonical_sha256, load_policy, policy_content_at_commit, verify_registered,
)
from quantlab.stats.sensitivity import (  # noqa: E402
    RunMetrics, TradeRecord, classify_sign, compare_variants, gap_exposure_diagnostic,
)

POLICY = ROOT / "config" / "data_quality_policy.toml"
REGISTRY = ROOT / "config" / "policy_registry.toml"
P = load_policy(POLICY)
TOL = P.zero_tolerance
FAKE_COMMIT = "0123456789abcdef0123456789abcdef01234567"


class TestPolicyFile(unittest.TestCase):

    def test_approved_values_are_exactly_as_agreed(self):
        self.assertEqual(P.version, "DQP-1")
        self.assertEqual(P.zero_tolerance, 1e-12)
        self.assertEqual((P.total_return.floor, P.total_return.relative), (0.01, 0.10))
        self.assertEqual((P.expectancy.floor, P.expectancy.relative), (0.0005, 0.10))
        self.assertEqual((P.max_drawdown.floor, P.max_drawdown.relative), (0.01, 0.10))
        self.assertEqual((P.trade_count.floor, P.trade_count.relative), (2, 0.05))
        self.assertEqual((P.gap_contribution.floor, P.gap_contribution.relative), (0.01, 0.10))
        self.assertEqual((P.gap_expectancy.floor, P.gap_expectancy.relative), (0.0005, 0.10))
        self.assertEqual(set(P.variant_b_masked_categories), {
            "partially_corroborated", "not_corroborated", "unverified",
            "primary_specific_flat", "cross_venue_quiet"})
        self.assertNotIn("corroborated_extreme", P.variant_b_masked_categories)
        r = P.raw
        self.assertEqual(r["missing_bars"]["fill"], "never")
        self.assertEqual(r["missing_bars"]["return_across_gap"], "undefined")
        self.assertIs(r["execution_after_gap"]["use_second_provider_prices"], False)
        self.assertEqual(r["execution_after_gap"]["adverse_gap_fill"], "first_open")
        self.assertEqual(r["execution_after_gap"]["favourable_gap_fill"], "limit_price")
        self.assertEqual(r["variants"]["official"], "A")

    def _variant(self, edit):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        text = POLICY.read_text(encoding="utf-8")
        path = tmp / "policy.toml"
        path.write_text(edit(text), encoding="utf-8")
        return path

    def test_unsafe_or_incomplete_policies_are_rejected(self):
        edits = {
            "fills bars": lambda t: t.replace('fill = "never"', 'fill = "interpolate"'),
            "uses second provider": lambda t: t.replace("use_second_provider_prices = false",
                                                        "use_second_provider_prices = true"),
            "official B": lambda t: t.replace('official = "A"', 'official = "B"'),
            "zero tolerance 0": lambda t: t.replace("zero_tolerance = 1e-12", "zero_tolerance = 0"),
            "zero tolerance huge": lambda t: t.replace("zero_tolerance = 1e-12", "zero_tolerance = 0.001"),
            "missing key": lambda t: t.replace("expectancy_floor = 0.0005\nexpectancy_relative = 0.10\nmax", "max"),
        }
        for name, edit in edits.items():
            with self.subTest(name), self.assertRaises(PolicyError):
                load_policy(self._variant(edit))

    def test_fingerprint_ignores_windows_line_endings(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        lf, crlf = tmp / "lf.toml", tmp / "crlf.toml"
        data = POLICY.read_bytes().replace(b"\r\n", b"\n")
        lf.write_bytes(data)
        crlf.write_bytes(data.replace(b"\n", b"\r\n"))
        self.assertEqual(canonical_sha256(lf), canonical_sha256(crlf))
        lf.write_bytes(data + b"# one changed character\n")
        self.assertNotEqual(canonical_sha256(lf), canonical_sha256(crlf))


class TestRegistry(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.policy = self.tmp / "policy.toml"
        shutil.copy(POLICY, self.policy)

    def registry(self, sha=None, commit=FAKE_COMMIT, version="DQP-1"):
        path = self.tmp / "registry.toml"
        path.write_text(f'[{version}]\ncommit = "{commit}"\nsha256 = "{sha or canonical_sha256(self.policy)}"\n',
                        encoding="utf-8")
        return path

    def test_registered_policy_verifies(self):
        identity = verify_registered(self.policy, self.registry())
        self.assertEqual(identity.as_dict(), {"policy_version": "DQP-1", "policy_commit": FAKE_COMMIT,
                                              "policy_sha256": canonical_sha256(self.policy)})

    def test_changed_policy_is_refused(self):
        reg = self.registry()
        self.policy.write_text(self.policy.read_text() + "\n# edited\n")
        with self.assertRaises(PolicyError) as ctx:
            verify_registered(self.policy, reg)
        self.assertIn("was changed", str(ctx.exception))

    def test_missing_or_bad_registry_is_refused(self):
        with self.assertRaises(PolicyError):
            verify_registered(self.policy, self.tmp / "nope.toml")
        with self.assertRaises(PolicyError):
            verify_registered(self.policy, self.registry(version="DQP-9"))
        with self.assertRaises(PolicyError):
            verify_registered(self.policy, self.registry(commit="abc123"))

    def test_real_registry_matches_policy_and_its_commit(self):
        """Runs once config/policy_registry.toml exists (the second commit)."""
        if not REGISTRY.exists():
            self.skipTest("registry not created yet (first DQP-1 commit)")
        identity = verify_registered(POLICY, REGISTRY)
        status, stored = policy_content_at_commit(ROOT, identity.commit, "config/data_quality_policy.toml")
        if status == "git_unavailable":
            self.skipTest("git is not available")
        self.assertEqual(status, "ok", f"registered commit check failed: {status}")
        self.assertEqual(stored, identity.sha256, "registered commit does not contain this policy")
        files = subprocess.run(["git", "ls-tree", "-r", "--name-only", identity.commit], cwd=ROOT,
                               capture_output=True, text=True).stdout.splitlines()
        self.assertFalse([f for f in files if f.startswith("src/") and "strateg" in f.lower()],
                         "strategy code must not exist in the DQP-1 commit")


class TestCommitLookup(unittest.TestCase):

    def test_lookup_distinguishes_every_failure(self):
        status, _ = policy_content_at_commit(ROOT, "f" * 40, "config/data_quality_policy.toml")
        if status == "git_unavailable":
            self.skipTest("git is not available")
        self.assertEqual(status, "commit_not_found")
        first = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=ROOT,
                               capture_output=True, text=True).stdout.split()
        if first:  # the very first commit predates the policy file
            self.assertEqual(policy_content_at_commit(ROOT, first[0], "config/data_quality_policy.toml")[0],
                             "file_not_in_commit")
        with tempfile.TemporaryDirectory() as tmp:  # a folder that is not a git repository
            self.assertEqual(policy_content_at_commit(Path(tmp), "f" * 40, "x")[0], "git_unavailable")


class TestZeroTolerance(unittest.TestCase):

    def test_rounding_noise_is_zero(self):
        for v in (0.0, -0.0, 1e-13, -1e-13, 1e-12, -1e-12, 0.1 + 0.2 - 0.3, -(0.1 + 0.2 - 0.3)):
            with self.subTest(v=v):
                self.assertEqual(classify_sign(v, TOL), 0)

    def test_values_beyond_tolerance_keep_their_sign(self):
        self.assertEqual(classify_sign(2e-12, TOL), 1)
        self.assertEqual(classify_sign(-2e-12, TOL), -1)
        # One cent on $10,000 is 1e-6: economically tiny but real, and NOT hidden.
        self.assertEqual(classify_sign(1e-6, TOL), 1)
        self.assertEqual(classify_sign(-1e-6, TOL), -1)

    def test_nan_is_rejected(self):
        with self.assertRaises(ValueError):
            classify_sign(float("nan"), TOL)


def m(r=0.10, e=0.002, d=0.20, n=40):
    return RunMetrics(total_return=r, expectancy=e, max_drawdown=d, trade_count=n)


class TestVariantSensitivity(unittest.TestCase):

    def test_identical_runs_are_not_sensitive(self):
        result = compare_variants(m(), m(), P)
        self.assertFalse(result.sensitive)
        self.assertEqual(result.label, "NOT SENSITIVE TO DATA QUALITY")

    def test_worked_examples(self):
        self.assertFalse(compare_variants(m(r=0.005), m(r=0.004), P).sensitive)   # floor protects
        self.assertTrue(compare_variants(m(r=0.50), m(r=0.43), P).sensitive)      # 0.07 > 0.05
        self.assertTrue(compare_variants(m(n=40), m(n=37), P).sensitive)          # 3 > max(2, 2.0)
        self.assertFalse(compare_variants(m(n=40), m(n=38), P).sensitive)         # 2 is not > 2

    def test_relative_part_protects_large_values(self):
        # 1.00 vs 0.92: difference 0.08 is above the 0.01 floor but within 10% of 1.00.
        self.assertFalse(compare_variants(m(r=1.00), m(r=0.92), P).sensitive)
        self.assertTrue(compare_variants(m(r=1.00), m(r=0.89), P).sensitive)      # 0.11 > 0.10
        self.assertFalse(compare_variants(m(n=400), m(n=385), P).sensitive)       # 15 <= 20
        self.assertTrue(compare_variants(m(n=400), m(n=379), P).sensitive)        # 21 > 20

    def test_each_metric_can_trigger(self):
        self.assertTrue(compare_variants(m(e=0.0030), m(e=0.0020), P).sensitive)  # 0.001 > 0.0005
        self.assertFalse(compare_variants(m(e=0.0030), m(e=0.0027), P).sensitive) # 0.0003 < 0.0005
        self.assertTrue(compare_variants(m(d=0.20), m(d=0.24), P).sensitive)      # 0.04 > 0.024
        self.assertFalse(compare_variants(m(d=0.20), m(d=0.205), P).sensitive)

    def test_sign_change_always_sensitive(self):
        result = compare_variants(m(r=0.002), m(r=-0.001), P)  # tiny, but a real sign change
        self.assertTrue(result.sensitive)
        self.assertTrue(any("sign of total return" in r for r in result.reasons))
        self.assertTrue(compare_variants(m(e=0.0001), m(e=-0.0001), P).sensitive)
        self.assertTrue(compare_variants(m(r=0.0), m(r=0.003), P).sensitive)       # zero vs positive

    def test_rounding_noise_never_creates_a_sign_flip(self):
        result = compare_variants(m(r=1e-15, e=-1e-16), m(r=-1e-15, e=1e-16), P)
        self.assertFalse(result.sensitive, result.reasons)


S0 = 10_000.0


def trades(non=(), gap=()):
    """non/gap: lists of (net_pnl, entry_value)."""
    return [TradeRecord(p, v, False) for p, v in non] + [TradeRecord(p, v, True) for p, v in gap]


class TestGapDiagnostic(unittest.TestCase):

    def diag(self, t):
        return gap_exposure_diagnostic(t, S0, P)

    def test_worked_example_1_not_sensitive(self):
        r = 0.0185  # every trade has the same return, so expectancy rules cannot trigger
        result = self.diag(trades(non=[(185, 185 / r)] * 10, gap=[(150, 150 / r)]))
        self.assertAlmostEqual(result["total_recorded_net_pnl"], 2000)
        self.assertEqual(result["verdict"], "GAP EXPOSURE NOT MATERIAL", result["reasons"])
        self.assertFalse(result["auditor_review_required"])

    def test_worked_example_2_rule_3(self):
        r = 0.0155
        result = self.diag(trades(non=[(155, 155 / r)] * 10, gap=[(450, 450 / r)]))
        self.assertEqual(result["verdict"], "GAP-EXPOSURE SENSITIVE")
        self.assertTrue(any(x.startswith("rule 3") for x in result["reasons"]))
        self.assertTrue(result["auditor_review_required"])

    def test_worked_example_3_rule_1_sign_flip(self):
        result = self.diag(trades(non=[(-10, 10_000)], gap=[(90, 10_000)]))
        self.assertAlmostEqual(result["total_recorded_net_pnl"], 80)
        self.assertTrue(result["subtracting_contribution_changes_net_pnl_sign"])
        self.assertTrue(any(x.startswith("rule 1") for x in result["reasons"]))

    def test_worked_example_4_not_sensitive(self):
        # P_all = +50, P_gap = -60: within $100, sign unchanged; many small non-gap
        # trades so one gap trade barely moves expectancy.
        result = self.diag(trades(non=[(5.5, 10_000)] * 20, gap=[(-60, 1_000_000)]))
        self.assertAlmostEqual(result["total_recorded_net_pnl"], 50)
        self.assertEqual(result["verdict"], "GAP EXPOSURE NOT MATERIAL", result["reasons"])

    def test_rule_2_expectancy_sign_flip(self):
        # Net P&L is dominated by one big non-gap winner (small return), while
        # many gap trades have negative returns: expectancy signs disagree.
        result = self.diag(trades(non=[(30, 1_000_000)], gap=[(-1, 100)] * 3))
        self.assertTrue(any(x.startswith("rule 2") for x in result["reasons"]))

    def test_rule_2_all_trades_gap_exposed(self):
        result = self.diag(trades(gap=[(10, 10_000)]))
        self.assertIsNone(result["non_gap_exposed_expectancy"])
        self.assertTrue(any("undefined" in x for x in result["reasons"]))

    def test_rule_4_material_expectancy_difference(self):
        r = self.diag(trades(non=[(10, 10_000)] * 10, gap=[(1, 100)]))  # 0.001 vs a 0.01 gap trade
        self.assertTrue(any(x.startswith("rule 4") for x in r["reasons"]))
        self.assertFalse(any(x.startswith("rule 1") or x.startswith("rule 3") for x in r["reasons"]))

    def test_no_gap_exposure(self):
        result = self.diag(trades(non=[(10, 10_000)] * 3))
        self.assertEqual(result["verdict"], "NO GAP EXPOSURE")
        self.assertEqual(result["gap_exposed_trades"], 0)
        self.assertEqual(result["reasons"], [])

    def test_reports_required_fields_and_no_counterfactuals(self):
        result = self.diag(trades(non=[(100, 10_000)], gap=[(50, 10_000)]))
        for key in ("gap_exposed_trades", "gap_exposed_net_pnl",
                    "gap_exposed_net_pnl_fraction_of_starting_equity",
                    "gap_exposed_share_of_total_net_pnl", "gap_exposed_expectancy",
                    "subtracting_contribution_changes_net_pnl_sign",
                    "non_gap_exposed_expectancy", "all_trades_expectancy"):
            self.assertIn(key, result)
        self.assertIn("DIAGNOSTIC CONTRIBUTION ANALYSIS", result["analysis"])
        for forbidden in ("total_return", "sharpe", "max_drawdown", "equity_curve"):
            self.assertFalse([k for k in result if forbidden in k.lower()], forbidden)
        self.assertAlmostEqual(result["gap_exposed_net_pnl_fraction_of_starting_equity"], 0.005)
        self.assertAlmostEqual(result["gap_exposed_share_of_total_net_pnl"], 50 / 150)

    def test_share_undefined_when_total_is_zero(self):
        result = self.diag(trades(non=[(-50, 10_000)], gap=[(50, 10_000)]))
        self.assertIsNone(result["gap_exposed_share_of_total_net_pnl"])

    def test_share_undefined_when_total_is_only_rounding_noise(self):
        result = self.diag(trades(non=[(-50, 10_000)], gap=[(50 + 1e-10, 10_000)]))
        self.assertIsNone(result["gap_exposed_share_of_total_net_pnl"])

    def test_rounding_noise_does_not_flip_net_pnl_sign(self):
        result = self.diag(trades(non=[(1e-9, 10_000)], gap=[(2e-9, 10_000)]))
        self.assertFalse(result["subtracting_contribution_changes_net_pnl_sign"])

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            gap_exposure_diagnostic([], 0, P)
        with self.assertRaises(ValueError):
            gap_exposure_diagnostic(trades(non=[(1, 0)]), S0, P)


class TestFirstBarAfterGap(unittest.TestCase):

    def test_long_rules(self):
        self.assertEqual(first_bar_after_gap(LONG, 90, stop=95, target=120).__dict__,
                         {"action": "exit_stop", "price": 90})      # adverse: fill at open
        self.assertEqual(first_bar_after_gap(LONG, 130, stop=95, target=120).__dict__,
                         {"action": "exit_target", "price": 120})   # favourable: no windfall
        self.assertEqual(first_bar_after_gap(LONG, 100, stop=95, target=120).action, "continue")
        self.assertEqual(first_bar_after_gap(LONG, 95, stop=95).__dict__, {"action": "exit_stop", "price": 95})
        self.assertEqual(first_bar_after_gap(LONG, 120, target=120).__dict__, {"action": "exit_target", "price": 120})

    def test_short_rules(self):
        self.assertEqual(first_bar_after_gap(SHORT, 110, stop=105, target=80).__dict__,
                         {"action": "exit_stop", "price": 110})
        self.assertEqual(first_bar_after_gap(SHORT, 70, stop=105, target=80).__dict__,
                         {"action": "exit_target", "price": 80})
        self.assertEqual(first_bar_after_gap(SHORT, 100, stop=105, target=80).action, "continue")
        self.assertEqual(first_bar_after_gap(SHORT, 105, stop=105).__dict__, {"action": "exit_stop", "price": 105})

    def test_long_and_short_are_exact_mirror_images(self):
        """Reflect every price around 1000: a LONG case must map onto the SHORT case."""
        mirror = lambda p: None if p is None else 2000 - p  # noqa: E731
        cases = 0
        for open_ in range(800, 1201, 25):
            for stop in (None, 850, 900, 950):
                for target in (None, 1050, 1100, 1150):
                    long_ = first_bar_after_gap(LONG, open_, stop, target)
                    short = first_bar_after_gap(SHORT, mirror(open_), mirror(stop), mirror(target))
                    self.assertEqual(long_.action, short.action, (open_, stop, target))
                    self.assertEqual(mirror(long_.price), short.price, (open_, stop, target))
                    cases += 1
        self.assertGreater(cases, 250)

    def test_invalid_inputs(self):
        bad = [(("FLAT", 100), {}), ((LONG, float("nan")), {}), ((LONG, -1), {}),
               ((LONG, 100), {"stop": 130, "target": 120}),   # long stop above target
               ((SHORT, 100), {"stop": 70, "target": 80}),     # short stop below target
               ((LONG, 100), {"stop": float("inf")})]
        for args, kwargs in bad:
            with self.subTest(args=args, kwargs=kwargs), self.assertRaises(ValueError):
                first_bar_after_gap(*args, **kwargs)


H = 3600


class TestGapPrimitives(unittest.TestCase):

    def test_segments_and_missing_before(self):
        times = [0, H, 2 * H, 5 * H, 6 * H, 8 * H]
        self.assertEqual(segments(times, H), [
            {"segment": 0, "missing_before": 0}, {"segment": 0, "missing_before": 0},
            {"segment": 0, "missing_before": 0}, {"segment": 1, "missing_before": 2},
            {"segment": 1, "missing_before": 0}, {"segment": 2, "missing_before": 1}])
        with self.assertRaises(ValueError):
            segments([0, H, H], H)

    def test_one_bar_return_undefined_across_gap(self):
        r = one_bar_returns([0, H, 3 * H, 4 * H], [100, 110, 121, 133.1], H)
        self.assertIsNone(r[0])
        self.assertAlmostEqual(r[1], 0.10)
        self.assertIsNone(r[2])                 # not a 1-hour return: a bar is missing
        self.assertAlmostEqual(r[3], 0.10)

    def test_gap_exposed(self):
        missing = [10 * H]
        self.assertTrue(is_gap_exposed(5 * H, 12 * H, missing, H))       # held through it
        self.assertTrue(is_gap_exposed(10.5 * H, 12 * H, missing, H))    # entered inside it
        self.assertFalse(is_gap_exposed(5 * H, 10 * H, missing, H))      # closed as it began
        self.assertFalse(is_gap_exposed(11 * H, 12 * H, missing, H))     # opened after it
        self.assertFalse(is_gap_exposed(5 * H, 12 * H, [], H))
        with self.assertRaises(ValueError):
            is_gap_exposed(5, 4, missing, H)


if __name__ == "__main__":
    unittest.main()
