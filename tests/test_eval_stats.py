"""Eval statistics on inputs whose answers are known."""

import pytest

from backend.eval.stats import bootstrap, brier, difference, paired, reliability, wilson


def test_bootstrap_mean_and_interval():
    est = bootstrap([1, 1, 1, 1])
    assert (est.mean, est.lo, est.hi, est.n) == (1, 1, 1, 4)  # no spread, no uncertainty
    est = bootstrap([0, 1] * 50)
    assert est.mean == 0.5 and 0.38 < est.lo < 0.45 and 0.55 < est.hi < 0.62  # ~±1.96·0.05
    assert bootstrap([]) is None
    assert bootstrap([3]).lo == bootstrap([3]).hi == 3


def test_bootstrap_is_reproducible():
    assert bootstrap([1, 5, 2, 8, 3], seed=7) == bootstrap([1, 5, 2, 8, 3], seed=7)


def test_paired_difference_uses_only_matching_pairs():
    on = {(0, 1): 1.0, (0, 2): 1.0, (1, 1): 1.0}
    off = {(0, 1): 0.0, (0, 2): 0.0, (9, 9): 5.0}
    est = paired(on, off)
    assert est.n == 2 and est.mean == 1.0 and est.excludes_zero()
    no_effect = paired({(0, i): 1.0 for i in range(10)}, {(0, i): 1.0 for i in range(10)})
    assert no_effect.mean == 0 and not no_effect.excludes_zero()


def test_wilson_interval_known_values():
    est = wilson(0, 15)
    assert est.mean == 0 and est.lo == 0 and est.hi == pytest.approx(0.2039, abs=1e-3)
    est = wilson(8, 10)
    assert est.lo == pytest.approx(0.4902, abs=1e-3) and est.hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson(0, 0) is None


def test_brier_score():
    assert brier([1.0, 0.0], [1, 0]) == 0  # perfectly confident and right
    assert brier([1.0], [0]) == 1  # perfectly confident and wrong
    assert brier([0.9, 0.9], [1, 0]) == pytest.approx((0.01 + 0.81) / 2)
    assert brier([], []) is None


def test_reliability_bins():
    bins = reliability([0.2, 0.96, 1.0, 0.97], [0, 1, 1, 0])
    top = bins[-1]
    assert (top.lo, top.hi, top.n) == (0.95, 1.0, 3)  # 1.0 lands in the last band
    assert top.accuracy == pytest.approx(2 / 3)
    assert bins[0].n == 1 and bins[0].accuracy == 0
    assert bins[2].n == 0 and bins[2].accuracy is None


def test_difference_of_independent_means():
    est = difference([1.0] * 20, [0.0] * 20)
    assert est.mean == 1 and est.lo == 1 and est.hi == 1 and est.excludes_zero()
    same = difference([0, 1] * 10, [1, 0] * 10)
    assert same.mean == 0 and not same.excludes_zero()
    assert difference([], [1.0]) is None
