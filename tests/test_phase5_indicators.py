"""Phase 5: reference indicators obey DQP-1 at gaps. Made-up numbers only."""

import unittest

import phase5_helpers as h
from quantlab.core.indicators import (
    ExponentialMovingAverage, SimpleMovingAverage, all_available,
)


class TestSimpleMovingAverage(unittest.TestCase):

    def test_hand_worked_values_and_window_restart_after_gap(self):
        sma = SimpleMovingAverage(3, h.H)
        # hours 0,1,2,3 contiguous: None, None, (1+2+3)/3 = 2, (2+3+4)/3 = 3
        self.assertEqual([sma.update(h.U(i), v) for i, v in enumerate([1, 2, 3, 4])],
                         [None, None, 2, 3])
        # hour 4 missing. Hours 5,6,7: window must be complete again -> None, None, 11
        self.assertEqual([sma.update(h.U(i), v) for i, v in ((5, 10), (6, 11), (7, 12))],
                         [None, None, 11])

    def test_window_never_spans_a_gap(self):
        sma = SimpleMovingAverage(2, h.H)
        sma.update(h.U(0), 100)
        self.assertIsNone(sma.update(h.U(2), 200))   # would be 150 if the gap were ignored
        self.assertEqual(sma.update(h.U(3), 300), 250)

    def test_bad_inputs_are_refused(self):
        sma = SimpleMovingAverage(2, h.H)
        sma.update(h.U(1), 1)
        for t in (h.U(1), h.U(0), h.U(1) + 60):
            with self.subTest(time=t), self.assertRaises(ValueError):
                sma.update(t, 1)
        for v in (float("nan"), float("inf"), None, True, "1"):
            with self.subTest(value=v), self.assertRaises(ValueError):
                sma.update(h.U(2), v)
        for w in (0, -1, 1.5):
            with self.assertRaises(ValueError):
                SimpleMovingAverage(w, h.H)


class TestExponentialMovingAverage(unittest.TestCase):

    def test_hand_worked_values_reset_and_rewarm(self):
        ema = ExponentialMovingAverage(3, h.H)     # alpha = 2 / 4 = 0.5
        # seed 2; 0.5*4 + 0.5*2 = 3; 0.5*6 + 0.5*3 = 4.5 (third bar: warm)
        self.assertEqual([ema.update(h.U(i), v) for i, v in enumerate([2, 4, 6])],
                         [None, None, 4.5])
        self.assertEqual(ema.update(h.U(3), 8), 6.25)          # 0.5*8 + 0.5*4.5
        # gap at hour 4: reset, seed 100, then 0.5*102+0.5*100 = 101, 0.5*104+0.5*101 = 102.5
        self.assertEqual([ema.update(h.U(i), v) for i, v in ((5, 100), (6, 102), (7, 104))],
                         [None, None, 102.5])

    def test_no_state_carried_across_gap(self):
        a, b = ExponentialMovingAverage(2, h.H), ExponentialMovingAverage(2, h.H)
        a.update(h.U(0), 1000)                      # history that must be forgotten
        for i, v in ((2, 10), (3, 20)):
            out_a = a.update(h.U(i), v)
            out_b = b.update(h.U(i), v)
        self.assertEqual(out_a, out_b)

    def test_all_available(self):
        self.assertTrue(all_available(1.0, 0.0))
        self.assertFalse(all_available(1.0, None))
        self.assertTrue(all_available())


if __name__ == "__main__":
    unittest.main()
